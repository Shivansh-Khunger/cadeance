import {
  integer,
  jsonb,
  pgTable,
  text,
  timestamp,
  uuid,
} from "drizzle-orm/pg-core";

import { users } from "./users";

export type TranscriptItem = {
  role: "user" | "assistant";
  text: string;
};

// Per-call cost approximation. `costCents` is rounded for display; `usage` is
// the raw breakdown (model rates × counts) so we can audit later if pricing
// changes.
export type CostBreakdown = {
  durationSeconds: number;
  llm?: { inputTokens: number; outputTokens: number; costCents: number };
  stt?: { audioSeconds: number; costCents: number };
  tts?: { characters: number; costCents: number };
  avatar?: { audioSeconds: number; costCents: number };
};

export const conversations = pgTable("conversations", {
  id: uuid("id").primaryKey().defaultRandom(),
  userId: uuid("user_id").references(() => users.id, { onDelete: "set null" }),
  roomName: text("room_name"),
  summary: text("summary"),
  intent: text("intent"),
  preferences: jsonb("preferences").$type<string[]>(),
  transcript: jsonb("transcript").$type<TranscriptItem[]>(),
  costCents: integer("cost_cents"),
  costBreakdown: jsonb("cost_breakdown").$type<CostBreakdown>(),
  startedAt: timestamp("started_at", { withTimezone: true }),
  endedAt: timestamp("ended_at", { withTimezone: true })
    .defaultNow()
    .notNull(),
  createdAt: timestamp("created_at", { withTimezone: true })
    .defaultNow()
    .notNull(),
});

export type Conversation = typeof conversations.$inferSelect;
export type NewConversation = typeof conversations.$inferInsert;
