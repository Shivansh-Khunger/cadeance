import { and, desc, eq, getTableColumns } from "drizzle-orm";
import { Hono } from "hono";
import { z } from "zod";

import { db } from "../config/database";
import { appointments, conversations, users } from "../db/schema";
import type { TranscriptItem } from "../db/schema";
import {
  generateSlots,
  isoDateSchema,
  isSlotValid,
  isUniqueViolation,
  isUuid,
  phoneSchema,
} from "./lib";

export const appointmentsRouter = new Hono();

// GET /users?phone=...
appointmentsRouter.get("/users", async (c) => {
  const phoneRaw = c.req.query("phone");
  if (!phoneRaw) return c.json({ error: "phone is required" }, 400);
  const parsed = phoneSchema.safeParse(phoneRaw);
  if (!parsed.success) return c.json({ error: "Invalid phone number" }, 400);

  const [user] = await db
    .select()
    .from(users)
    .where(eq(users.phone, parsed.data))
    .limit(1);
  if (!user) return c.json({ error: "User not found" }, 404);
  return c.json({ user }, 200);
});

// POST /users/identify
const identifyBody = z.object({
  phone: phoneSchema,
  name: z.string().min(1).max(100).optional(),
});

const RECENT_CONVERSATIONS_LIMIT = 5;
async function fetchRecentConversations(userId: string) {
  return db
    .select({
      id: conversations.id,
      summary: conversations.summary,
      intent: conversations.intent,
      preferences: conversations.preferences,
      endedAt: conversations.endedAt,
    })
    .from(conversations)
    .where(eq(conversations.userId, userId))
    .orderBy(desc(conversations.endedAt))
    .limit(RECENT_CONVERSATIONS_LIMIT);
}

appointmentsRouter.post("/users/identify", async (c) => {
  const raw = await c.req.json().catch(() => ({}));
  const parsed = identifyBody.safeParse(raw);
  if (!parsed.success) return c.json({ error: "Invalid request body" }, 400);
  const { phone, name } = parsed.data;

  const [existing] = await db
    .select()
    .from(users)
    .where(eq(users.phone, phone))
    .limit(1);

  if (!existing) {
    const [created] = await db
      .insert(users)
      .values({ phone, name: name ?? null })
      .returning();
    return c.json(
      { user: created, isNew: true, recentConversations: [] },
      201,
    );
  }

  // Existing user: fill in name lazily if it wasn't set, or overwrite if a
  // new one was provided.
  let user = existing;
  if (name && name !== existing.name) {
    const [updated] = await db
      .update(users)
      .set({ name })
      .where(eq(users.id, existing.id))
      .returning();
    user = updated;
  }
  const recentConversations = await fetchRecentConversations(user.id);
  return c.json({ user, isNew: false, recentConversations }, 200);
});

// GET /slots?days=14
const slotsQuery = z.object({
  days: z.coerce.number().int().min(1).max(60).default(14),
});
appointmentsRouter.get("/slots", async (c) => {
  const parsed = slotsQuery.safeParse({ days: c.req.query("days") });
  if (!parsed.success) return c.json({ error: "Invalid query" }, 400);

  const candidates = generateSlots(parsed.data.days);
  const booked = await db
    .select({ startsAt: appointments.startsAt })
    .from(appointments)
    .where(eq(appointments.status, "booked"));
  const taken = new Set(booked.map((r) => r.startsAt.toISOString()));
  const slots = candidates
    .map((d) => d.toISOString())
    .filter((iso) => !taken.has(iso));
  return c.json({ slots }, 200);
});

// POST /appointments
const bookBody = z.object({
  phone: phoneSchema,
  startsAt: isoDateSchema,
  notes: z.string().max(500).optional(),
});
appointmentsRouter.post("/appointments", async (c) => {
  const raw = await c.req.json().catch(() => ({}));
  const parsed = bookBody.safeParse(raw);
  if (!parsed.success) return c.json({ error: "Invalid request body" }, 400);
  const { phone, startsAt, notes } = parsed.data;

  if (!isSlotValid(startsAt)) {
    return c.json({ error: "Slot is not within bookable hours" }, 400);
  }

  const [user] = await db
    .select()
    .from(users)
    .where(eq(users.phone, phone))
    .limit(1);
  if (!user) {
    return c.json(
      {
        error:
          "User not found. Identify the caller first via /users/identify.",
      },
      404,
    );
  }

  try {
    const [appointment] = await db
      .insert(appointments)
      .values({
        userId: user.id,
        startsAt: new Date(startsAt),
        notes: notes ?? null,
      })
      .returning();
    return c.json({ appointment, user }, 201);
  } catch (err) {
    if (isUniqueViolation(err)) {
      return c.json({ error: "Slot already booked" }, 409);
    }
    console.error("book_appointment failed", err);
    return c.json({ error: "Failed to book appointment" }, 500);
  }
});

// GET /appointments?phone=...
appointmentsRouter.get("/appointments", async (c) => {
  const phoneRaw = c.req.query("phone");
  if (!phoneRaw) return c.json({ error: "phone is required" }, 400);
  const phoneParsed = phoneSchema.safeParse(phoneRaw);
  if (!phoneParsed.success) {
    return c.json({ error: "Invalid phone number" }, 400);
  }

  const [user] = await db
    .select()
    .from(users)
    .where(eq(users.phone, phoneParsed.data))
    .limit(1);
  if (!user) return c.json({ error: "User not found" }, 404);

  const rows = await db
    .select()
    .from(appointments)
    .where(eq(appointments.userId, user.id))
    .orderBy(desc(appointments.startsAt));
  return c.json({ appointments: rows, user }, 200);
});

// PATCH /appointments/:id?phone=...
const patchBody = z.object({
  startsAt: isoDateSchema.optional(),
  notes: z.string().max(500).nullable().optional(),
});
appointmentsRouter.patch("/appointments/:id", async (c) => {
  const id = c.req.param("id");
  if (!isUuid(id)) return c.json({ error: "Invalid id" }, 400);

  const phoneRaw = c.req.query("phone");
  if (!phoneRaw) return c.json({ error: "phone is required" }, 400);
  const phoneParsed = phoneSchema.safeParse(phoneRaw);
  if (!phoneParsed.success) {
    return c.json({ error: "Invalid phone number" }, 400);
  }

  const raw = await c.req.json().catch(() => ({}));
  const bodyParsed = patchBody.safeParse(raw);
  if (!bodyParsed.success) {
    return c.json({ error: "Invalid request body" }, 400);
  }
  const update = bodyParsed.data;
  if (update.startsAt && !isSlotValid(update.startsAt)) {
    return c.json({ error: "Slot is not within bookable hours" }, 400);
  }

  const [user] = await db
    .select()
    .from(users)
    .where(eq(users.phone, phoneParsed.data))
    .limit(1);
  if (!user) return c.json({ error: "User not found" }, 404);

  const [existing] = await db
    .select()
    .from(appointments)
    .where(and(eq(appointments.id, id), eq(appointments.userId, user.id)))
    .limit(1);
  if (!existing) return c.json({ error: "Appointment not found" }, 404);
  if (existing.status === "cancelled") {
    return c.json({ error: "Cannot modify a cancelled appointment" }, 400);
  }

  const patch: { startsAt?: Date; notes?: string | null; updatedAt: Date } = {
    updatedAt: new Date(),
  };
  if (update.startsAt) patch.startsAt = new Date(update.startsAt);
  if (update.notes !== undefined) patch.notes = update.notes;

  try {
    const [updated] = await db
      .update(appointments)
      .set(patch)
      .where(eq(appointments.id, id))
      .returning();
    return c.json({ appointment: updated, user }, 200);
  } catch (err) {
    if (isUniqueViolation(err)) {
      return c.json({ error: "Slot already booked" }, 409);
    }
    console.error("modify_appointment failed", err);
    return c.json({ error: "Failed to modify appointment" }, 500);
  }
});

// DELETE /appointments/:id?phone=...
appointmentsRouter.delete("/appointments/:id", async (c) => {
  const id = c.req.param("id");
  if (!isUuid(id)) return c.json({ error: "Invalid id" }, 400);

  const phoneRaw = c.req.query("phone");
  if (!phoneRaw) return c.json({ error: "phone is required" }, 400);
  const phoneParsed = phoneSchema.safeParse(phoneRaw);
  if (!phoneParsed.success) {
    return c.json({ error: "Invalid phone number" }, 400);
  }

  const [user] = await db
    .select()
    .from(users)
    .where(eq(users.phone, phoneParsed.data))
    .limit(1);
  if (!user) return c.json({ error: "User not found" }, 404);

  const [updated] = await db
    .update(appointments)
    .set({ status: "cancelled", updatedAt: new Date() })
    .where(and(eq(appointments.id, id), eq(appointments.userId, user.id)))
    .returning();
  if (!updated) return c.json({ error: "Appointment not found" }, 404);
  return c.json({ appointment: updated, user }, 200);
});

// POST /conversations — agent posts the full call payload at the end.
const transcriptItemSchema = z.object({
  role: z.enum(["user", "assistant"]),
  text: z.string(),
});

const costBreakdownSchema = z
  .object({
    durationSeconds: z.number().nonnegative(),
    llm: z
      .object({
        inputTokens: z.number().int().nonnegative(),
        outputTokens: z.number().int().nonnegative(),
        costCents: z.number().nonnegative(),
      })
      .optional(),
    stt: z
      .object({
        audioSeconds: z.number().nonnegative(),
        costCents: z.number().nonnegative(),
      })
      .optional(),
    tts: z
      .object({
        characters: z.number().int().nonnegative(),
        costCents: z.number().nonnegative(),
      })
      .optional(),
    avatar: z
      .object({
        audioSeconds: z.number().nonnegative(),
        costCents: z.number().nonnegative(),
      })
      .optional(),
  })
  .strict();

const conversationBody = z.object({
  phone: phoneSchema.optional(),
  roomName: z.string().max(200).optional(),
  summary: z.string().max(4000).optional(),
  intent: z.string().max(64).optional(),
  preferences: z.array(z.string().max(280)).max(20).optional(),
  transcript: z.array(transcriptItemSchema).max(500).optional(),
  startedAt: isoDateSchema.optional(),
  costCents: z.number().int().nonnegative().max(1_000_000).optional(),
  costBreakdown: costBreakdownSchema.optional(),
});

appointmentsRouter.post("/conversations", async (c) => {
  const raw = await c.req.json().catch(() => ({}));
  const parsed = conversationBody.safeParse(raw);
  if (!parsed.success) {
    return c.json({ error: "Invalid request body" }, 400);
  }
  const data = parsed.data;

  let userId: string | null = null;
  if (data.phone) {
    const [user] = await db
      .select({ id: users.id })
      .from(users)
      .where(eq(users.phone, data.phone))
      .limit(1);
    userId = user?.id ?? null;
  }

  const transcript: TranscriptItem[] = data.transcript ?? [];

  const [row] = await db
    .insert(conversations)
    .values({
      userId,
      roomName: data.roomName ?? null,
      summary: data.summary ?? null,
      intent: data.intent ?? null,
      preferences: data.preferences ?? [],
      transcript,
      costCents: data.costCents ?? null,
      costBreakdown: data.costBreakdown ?? null,
      startedAt: data.startedAt ? new Date(data.startedAt) : null,
    })
    .returning();
  return c.json({ conversation: row }, 201);
});

// GET /conversations/by-room/:roomName — used by the web app to rebuild the
// summary modal after a hard disconnect (no `cadence.summary` topic publish).
appointmentsRouter.get("/conversations/by-room/:roomName", async (c) => {
  const roomName = c.req.param("roomName");
  if (!roomName) return c.json({ error: "roomName required" }, 400);

  const rows = await db
    .select({
      ...getTableColumns(conversations),
      user: {
        id: users.id,
        phone: users.phone,
        name: users.name,
      },
    })
    .from(conversations)
    .leftJoin(users, eq(conversations.userId, users.id))
    .where(eq(conversations.roomName, roomName))
    .orderBy(desc(conversations.endedAt))
    .limit(1);
  const row = rows[0];
  if (!row) return c.json({ error: "Not found" }, 404);

  let userAppointments: (typeof appointments.$inferSelect)[] = [];
  if (row.user?.id) {
    userAppointments = await db
      .select()
      .from(appointments)
      .where(eq(appointments.userId, row.user.id))
      .orderBy(desc(appointments.startsAt));
  }
  return c.json({ conversation: row, appointments: userAppointments }, 200);
});
