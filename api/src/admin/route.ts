import {
  and,
  count,
  desc,
  eq,
  getTableColumns,
  gt,
  sql,
  sum,
} from "drizzle-orm";
import { Hono } from "hono";

import { db } from "../config/database";
import { appointments, conversations, users } from "../db/schema";

export const adminRouter = new Hono();

adminRouter.get("/stats", async (c) => {
  const [
    usersRows,
    apptsRows,
    bookedRows,
    cancelledRows,
    upcomingRows,
    conversationsRows,
    costRows,
  ] = await Promise.all([
    db.select({ value: count() }).from(users),
    db.select({ value: count() }).from(appointments),
    db
      .select({ value: count() })
      .from(appointments)
      .where(eq(appointments.status, "booked")),
    db
      .select({ value: count() })
      .from(appointments)
      .where(eq(appointments.status, "cancelled")),
    db
      .select({ value: count() })
      .from(appointments)
      .where(
        and(
          eq(appointments.status, "booked"),
          gt(appointments.startsAt, sql`NOW()`),
        ),
      ),
    db.select({ value: count() }).from(conversations),
    db
      .select({
        total: sum(conversations.costCents).mapWith(Number),
        priced: count(conversations.costCents),
      })
      .from(conversations),
  ]);

  const totalCostCents = Number(costRows[0]?.total ?? 0) || 0;
  const pricedCount = Number(costRows[0]?.priced ?? 0) || 0;
  const avgCostCents =
    pricedCount > 0 ? Math.round(totalCostCents / pricedCount) : 0;

  return c.json({
    users: usersRows[0]?.value ?? 0,
    appointments: apptsRows[0]?.value ?? 0,
    booked: bookedRows[0]?.value ?? 0,
    cancelled: cancelledRows[0]?.value ?? 0,
    upcoming: upcomingRows[0]?.value ?? 0,
    conversations: conversationsRows[0]?.value ?? 0,
    totalCostCents,
    avgCostCents,
  });
});

adminRouter.get("/users", async (c) => {
  const limit = clampInt(c.req.query("limit"), 100, 1, 500);
  const offset = clampInt(c.req.query("offset"), 0, 0, Number.MAX_SAFE_INTEGER);
  const rows = await db
    .select({
      ...getTableColumns(users),
      appointmentCount: sql<number>`COUNT(${appointments.id})::int`,
      bookedCount: sql<number>`(COUNT(${appointments.id}) FILTER (WHERE ${appointments.status} = 'booked'))::int`,
    })
    .from(users)
    .leftJoin(appointments, eq(appointments.userId, users.id))
    .groupBy(users.id)
    .orderBy(desc(users.createdAt))
    .limit(limit)
    .offset(offset);
  return c.json({ users: rows });
});

adminRouter.get("/appointments", async (c) => {
  const limit = clampInt(c.req.query("limit"), 100, 1, 500);
  const offset = clampInt(c.req.query("offset"), 0, 0, Number.MAX_SAFE_INTEGER);
  const statusRaw = c.req.query("status");
  const status =
    statusRaw === "booked" || statusRaw === "cancelled" ? statusRaw : undefined;

  const rows = await db
    .select({
      ...getTableColumns(appointments),
      user: {
        id: users.id,
        phone: users.phone,
        name: users.name,
      },
    })
    .from(appointments)
    .innerJoin(users, eq(appointments.userId, users.id))
    .where(status ? eq(appointments.status, status) : undefined)
    .orderBy(desc(appointments.startsAt))
    .limit(limit)
    .offset(offset);
  return c.json({ appointments: rows });
});

adminRouter.get("/conversations", async (c) => {
  const limit = clampInt(c.req.query("limit"), 100, 1, 500);
  const offset = clampInt(c.req.query("offset"), 0, 0, Number.MAX_SAFE_INTEGER);
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
    .orderBy(desc(conversations.endedAt))
    .limit(limit)
    .offset(offset);
  return c.json({ conversations: rows });
});

function clampInt(
  raw: string | undefined,
  fallback: number,
  min: number,
  max: number,
): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(Math.max(Math.floor(n), min), max);
}
