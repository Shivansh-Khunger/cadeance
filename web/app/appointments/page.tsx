"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";

type User = {
  id: string;
  phone: string;
  name: string | null;
  createdAt: string;
};

type AppointmentStatus = "booked" | "cancelled";

type Appointment = {
  id: string;
  userId: string;
  startsAt: string;
  status: AppointmentStatus;
  notes: string | null;
  createdAt: string;
  updatedAt: string;
};

const API = process.env.NEXT_PUBLIC_API_URL ?? "";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let message = text;
    try {
      const parsed = JSON.parse(text) as { error?: string };
      if (parsed.error) message = parsed.error;
    } catch {
      // not JSON, fall through
    }
    throw new Error(message || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

function formatSlot(iso: string): string {
  const d = new Date(iso);
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(d);
}

export default function AppointmentsPage() {
  const [phoneInput, setPhoneInput] = useState("");
  const [phone, setPhone] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [slots, setSlots] = useState<string[]>([]);
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [movingId, setMovingId] = useState<string | null>(null);

  async function refreshAll(p: string) {
    const [slotsRes, apptsRes] = await Promise.all([
      apiFetch<{ slots: string[] }>("/slots"),
      apiFetch<{ appointments: Appointment[]; user: User }>(
        `/appointments?phone=${encodeURIComponent(p)}`,
      ),
    ]);
    setSlots(slotsRes.slots);
    setAppointments(apptsRes.appointments);
    setUser(apptsRes.user);
  }

  async function identify(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading("identify");
    try {
      const data = await apiFetch<{ user: User; isNew: boolean }>(
        "/users/identify",
        {
          method: "POST",
          body: JSON.stringify({ phone: phoneInput }),
        },
      );
      setUser(data.user);
      setPhone(data.user.phone);
      await refreshAll(data.user.phone);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setLoading(null);
    }
  }

  async function saveName(name: string) {
    if (!user) return;
    setError(null);
    setLoading("name");
    try {
      const data = await apiFetch<{ user: User }>("/users/identify", {
        method: "POST",
        body: JSON.stringify({ phone: user.phone, name }),
      });
      setUser(data.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setLoading(null);
    }
  }

  async function book(slot: string) {
    if (!phone) return;
    setError(null);
    setLoading(`book:${slot}`);
    try {
      await apiFetch("/appointments", {
        method: "POST",
        body: JSON.stringify({ phone, startsAt: slot }),
      });
      await refreshAll(phone);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setLoading(null);
    }
  }

  async function cancel(id: string) {
    if (!phone) return;
    setError(null);
    setLoading(`cancel:${id}`);
    try {
      await apiFetch(
        `/appointments/${id}?phone=${encodeURIComponent(phone)}`,
        { method: "DELETE" },
      );
      await refreshAll(phone);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setLoading(null);
    }
  }

  async function move(id: string, newSlot: string) {
    if (!phone) return;
    setError(null);
    setLoading(`move:${id}`);
    try {
      await apiFetch(
        `/appointments/${id}?phone=${encodeURIComponent(phone)}`,
        {
          method: "PATCH",
          body: JSON.stringify({ startsAt: newSlot }),
        },
      );
      setMovingId(null);
      await refreshAll(phone);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setLoading(null);
    }
  }

  function reset() {
    setPhone(null);
    setUser(null);
    setSlots([]);
    setAppointments([]);
    setPhoneInput("");
    setMovingId(null);
    setError(null);
  }

  return (
    <main className="ambient flex flex-1 flex-col items-center px-6 py-12">
      <div className="flex w-full max-w-3xl flex-col gap-10">
        <header className="flex flex-col items-start gap-3">
          <span className="inline-flex items-center gap-2 rounded-full border border-(--border) px-3 py-1 text-xs font-medium text-(--muted-strong)">
            <span className="size-1.5 rounded-full bg-(--accent)" />
            Dev test surface
          </span>
          <h1 className="text-balance text-4xl font-semibold tracking-tight sm:text-5xl">
            Appointments
          </h1>
          <p className="text-pretty text-(--muted-strong)">
            Exercise the booking endpoints by hand before wiring them into the
            agent.
          </p>
        </header>

        <AnimatePresence>
          {error ? (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className="rounded-2xl border border-(--accent) bg-[color-mix(in_oklab,_var(--accent)_10%,_transparent)] px-4 py-3 text-sm"
            >
              {error}
            </motion.div>
          ) : null}
        </AnimatePresence>

        <Section number="1" title="Identify">
          {!phone ? (
            <form
              onSubmit={identify}
              className="flex flex-col gap-3 sm:flex-row"
            >
              <input
                type="tel"
                inputMode="tel"
                placeholder="+15551234567"
                value={phoneInput}
                onChange={(e) => setPhoneInput(e.target.value)}
                required
                className="h-12 flex-1 rounded-full border border-(--border) bg-(--surface) px-5 text-base outline-none focus:border-(--foreground)"
              />
              <button
                type="submit"
                disabled={
                  loading === "identify" ||
                  phoneInput.replace(/\D/g, "").length < 7
                }
                className="inline-flex h-12 items-center justify-center rounded-full bg-foreground px-8 text-base font-medium text-background transition-colors hover:bg-foreground/85 disabled:opacity-50"
              >
                {loading === "identify" ? "Identifying…" : "Identify"}
              </button>
            </form>
          ) : (
            <UserCard
              key={user?.id ?? phone}
              user={user!}
              onName={saveName}
              loading={loading === "name"}
              onReset={reset}
            />
          )}
        </Section>

        {phone ? (
          <Section number="2" title="Available slots">
            {slots.length === 0 ? (
              <p className="text-sm text-(--muted-strong)">
                No slots available right now.
              </p>
            ) : (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {slots.slice(0, 24).map((s) => {
                  const busy = loading === `book:${s}`;
                  return (
                    <button
                      key={s}
                      onClick={() => book(s)}
                      disabled={busy || loading !== null}
                      className="rounded-2xl border border-(--border) bg-(--surface) px-4 py-3 text-left text-sm transition-colors hover:border-(--foreground) disabled:opacity-50"
                    >
                      <div className="font-medium">{formatSlot(s)}</div>
                      <div className="mt-0.5 text-xs text-(--muted)">
                        {busy ? "Booking…" : "Tap to book"}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
            {slots.length > 24 ? (
              <p className="mt-2 text-xs text-(--muted)">
                Showing first 24 of {slots.length} available slots.
              </p>
            ) : null}
          </Section>
        ) : null}

        {phone ? (
          <Section number="3" title="Your appointments">
            {appointments.length === 0 ? (
              <p className="text-sm text-(--muted-strong)">
                No appointments yet.
              </p>
            ) : (
              <ul className="flex flex-col gap-3">
                <AnimatePresence initial={false}>
                  {appointments.map((a) => {
                    const isCancelled = a.status === "cancelled";
                    return (
                      <motion.li
                        key={a.id}
                        layout
                        initial={{ opacity: 0, y: 4 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -4 }}
                        className={`rounded-2xl border border-(--border) bg-(--surface) p-4 ${
                          isCancelled ? "opacity-60" : ""
                        }`}
                      >
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <div className="flex flex-col">
                            <span
                              className={`font-medium ${isCancelled ? "line-through" : ""}`}
                            >
                              {formatSlot(a.startsAt)}
                            </span>
                            <span className="text-xs text-(--muted)">
                              {isCancelled ? "Cancelled" : "Booked"}
                            </span>
                          </div>
                          {!isCancelled ? (
                            <div className="flex gap-2">
                              <button
                                onClick={() =>
                                  setMovingId(movingId === a.id ? null : a.id)
                                }
                                disabled={
                                  loading !== null &&
                                  loading !== `move:${a.id}`
                                }
                                className="rounded-full border border-(--border) px-4 py-1.5 text-sm font-medium transition-colors hover:bg-foreground/5 disabled:opacity-50"
                              >
                                {movingId === a.id ? "Cancel move" : "Move"}
                              </button>
                              <button
                                onClick={() => cancel(a.id)}
                                disabled={loading !== null}
                                className="rounded-full border border-(--border) px-4 py-1.5 text-sm font-medium text-(--accent) transition-colors hover:bg-[color-mix(in_oklab,_var(--accent)_10%,_transparent)] disabled:opacity-50"
                              >
                                {loading === `cancel:${a.id}`
                                  ? "Cancelling…"
                                  : "Cancel"}
                              </button>
                            </div>
                          ) : null}
                        </div>
                        <AnimatePresence>
                          {movingId === a.id && !isCancelled ? (
                            <motion.div
                              initial={{ opacity: 0, height: 0 }}
                              animate={{ opacity: 1, height: "auto" }}
                              exit={{ opacity: 0, height: 0 }}
                              className="mt-3 grid grid-cols-2 gap-2 overflow-hidden sm:grid-cols-3"
                            >
                              {slots.slice(0, 12).map((s) => (
                                <button
                                  key={s}
                                  onClick={() => move(a.id, s)}
                                  disabled={loading === `move:${a.id}`}
                                  className="rounded-xl border border-(--border) bg-(--background) px-3 py-2 text-xs transition-colors hover:border-(--foreground) disabled:opacity-50"
                                >
                                  {formatSlot(s)}
                                </button>
                              ))}
                            </motion.div>
                          ) : null}
                        </AnimatePresence>
                      </motion.li>
                    );
                  })}
                </AnimatePresence>
              </ul>
            )}
          </Section>
        ) : null}
      </div>
    </main>
  );
}

function Section({
  number,
  title,
  children,
}: {
  number: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-baseline gap-3">
        <span className="font-mono text-xs uppercase tracking-widest text-(--muted)">
          Step {number}
        </span>
        <h2 className="text-2xl font-semibold tracking-tight">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function UserCard({
  user,
  onName,
  onReset,
  loading,
}: {
  user: User;
  onName: (name: string) => void;
  onReset: () => void;
  loading: boolean;
}) {
  const [name, setName] = useState(user.name ?? "");
  const dirty = name.trim() && name.trim() !== (user.name ?? "");

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-(--border) bg-(--surface) p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex flex-col gap-1">
        <span className="text-xs text-(--muted)">Phone</span>
        <span className="font-mono text-sm">{user.phone}</span>
      </div>
      <div className="flex flex-1 items-center gap-2 sm:max-w-sm">
        <input
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="h-10 flex-1 rounded-full border border-(--border) bg-(--background) px-4 text-sm outline-none focus:border-(--foreground)"
        />
        <button
          onClick={() => onName(name.trim())}
          disabled={!dirty || loading}
          className="rounded-full border border-(--border) px-4 py-1.5 text-sm font-medium transition-colors hover:bg-foreground/5 disabled:opacity-40"
        >
          {loading ? "…" : "Save"}
        </button>
      </div>
      <button
        onClick={onReset}
        className="rounded-full border border-(--border) px-4 py-1.5 text-xs font-medium text-(--muted-strong) transition-colors hover:bg-foreground/5"
      >
        Switch user
      </button>
    </div>
  );
}
