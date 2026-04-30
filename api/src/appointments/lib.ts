import { z } from "zod";

const SLOT_HOUR_START = 9; // first slot at 09:00
const SLOT_HOUR_END = 16; // last slot at 16:30
const SLOT_STEP_MINUTES = 30;
export const DEFAULT_DAYS = 14;

export function normalizePhone(raw: string): string | null {
  const cleaned = raw.replace(/[^\d+]/g, "");
  const digitsOnly = cleaned.replace(/\D/g, "");
  if (digitsOnly.length < 7) return null;
  return cleaned;
}

export function generateSlots(daysAhead = DEFAULT_DAYS): Date[] {
  const slots: Date[] = [];
  const now = new Date();
  for (let d = 0; d < daysAhead; d++) {
    const day = new Date(now);
    day.setDate(day.getDate() + d);
    day.setHours(0, 0, 0, 0);
    const dow = day.getDay();
    if (dow === 0 || dow === 6) continue; // skip Sun/Sat
    for (let h = SLOT_HOUR_START; h <= SLOT_HOUR_END; h++) {
      for (let m = 0; m < 60; m += SLOT_STEP_MINUTES) {
        if (h === SLOT_HOUR_END && m > 30) continue;
        const slot = new Date(day);
        slot.setHours(h, m, 0, 0);
        if (slot <= now) continue;
        slots.push(slot);
      }
    }
  }
  return slots;
}

export function isSlotValid(iso: string, daysAhead = DEFAULT_DAYS): boolean {
  const target = new Date(iso);
  if (isNaN(target.getTime())) return false;
  const now = new Date();
  if (target <= now) return false;

  const horizon = new Date(now);
  horizon.setDate(horizon.getDate() + daysAhead);
  if (target > horizon) return false;

  const dow = target.getDay();
  if (dow === 0 || dow === 6) return false;

  if (target.getSeconds() !== 0 || target.getMilliseconds() !== 0) return false;
  const m = target.getMinutes();
  if (m !== 0 && m !== 30) return false;
  const h = target.getHours();
  if (h < SLOT_HOUR_START || h > SLOT_HOUR_END) return false;
  if (h === SLOT_HOUR_END && m > 30) return false;
  return true;
}

export const phoneSchema = z
  .string()
  .min(1)
  .transform((s, ctx) => {
    const normalized = normalizePhone(s);
    if (!normalized) {
      ctx.addIssue({ code: "custom", message: "Invalid phone number" });
      return z.NEVER;
    }
    return normalized;
  });

export const isoDateSchema = z.string().refine(
  (s) => !isNaN(new Date(s).getTime()),
  { message: "Invalid ISO date" },
);

export function isUuid(s: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    s,
  );
}

// Postgres SQLSTATE 23505 = unique_violation. Drizzle may wrap the original
// error on `.cause`, so check both layers.
export function isUniqueViolation(err: unknown): boolean {
  for (const e of [err, (err as { cause?: unknown })?.cause]) {
    if (typeof e !== "object" || e === null) continue;
    const code = (e as { code?: string }).code;
    if (code === "23505") return true;
  }
  return false;
}
