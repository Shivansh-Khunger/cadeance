import { sql } from "drizzle-orm";
import {
  pgEnum,
  pgTable,
  text,
  timestamp,
  uniqueIndex,
  uuid,
} from "drizzle-orm/pg-core";

import { users } from "./users";

export const appointmentStatus = pgEnum("appointment_status", [
  "booked",
  "cancelled",
]);

export const appointments = pgTable(
  "appointments",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    userId: uuid("user_id")
      .references(() => users.id, { onDelete: "cascade" })
      .notNull(),
    startsAt: timestamp("starts_at", { withTimezone: true }).notNull(),
    status: appointmentStatus("status").notNull().default("booked"),
    notes: text("notes"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
  },
  // Partial unique index: at most one row with status='booked' per starts_at.
  // Prevents double-booking at the DB level (race-safe).
  (t) => [
    uniqueIndex("appointments_active_slot_unique")
      .on(t.startsAt)
      .where(sql`${t.status} = 'booked'`),
  ],
);

export type Appointment = typeof appointments.$inferSelect;
export type NewAppointment = typeof appointments.$inferInsert;
