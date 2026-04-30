"use client";

import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";

import { Header } from "../_components/Header";

const API = process.env.NEXT_PUBLIC_API_URL ?? "";

type Stats = {
  users: number;
  appointments: number;
  booked: number;
  cancelled: number;
  upcoming: number;
  conversations: number;
  totalCostCents: number;
  avgCostCents: number;
};

type CostBreakdown = {
  durationSeconds: number;
  llm?: { inputTokens: number; outputTokens: number; costCents: number };
  stt?: { audioSeconds: number; costCents: number };
  tts?: { characters: number; costCents: number };
  avatar?: { audioSeconds: number; costCents: number };
};

type AdminUser = {
  id: string;
  phone: string;
  name: string | null;
  createdAt: string;
  appointmentCount: number;
  bookedCount: number;
};

type AppointmentStatus = "booked" | "cancelled";

type AdminAppointment = {
  id: string;
  userId: string;
  startsAt: string;
  status: AppointmentStatus;
  notes: string | null;
  createdAt: string;
  updatedAt: string;
  user: { id: string; phone: string; name: string | null };
};

type TranscriptItem = { role: "user" | "assistant"; text: string };

type AdminConversation = {
  id: string;
  userId: string | null;
  roomName: string | null;
  summary: string | null;
  intent: string | null;
  preferences: string[] | null;
  transcript: TranscriptItem[] | null;
  costCents: number | null;
  costBreakdown: CostBreakdown | null;
  startedAt: string | null;
  endedAt: string;
  createdAt: string;
  user: { id: string; phone: string; name: string | null } | null;
};

async function adminFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

type Tab = "users" | "appointments" | "conversations";
type StatusFilter = "all" | AppointmentStatus;

export default function AdminPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [appts, setAppts] = useState<AdminAppointment[]>([]);
  const [conversations, setConversations] = useState<AdminConversation[]>([]);
  const [tab, setTab] = useState<Tab>("conversations");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey((n) => n + 1), []);

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      try {
        const apptPath =
          statusFilter === "all"
            ? "/admin/appointments"
            : `/admin/appointments?status=${statusFilter}`;
        const [s, u, a, c] = await Promise.all([
          adminFetch<Stats>("/admin/stats"),
          adminFetch<{ users: AdminUser[] }>("/admin/users"),
          adminFetch<{ appointments: AdminAppointment[] }>(apptPath),
          adminFetch<{ conversations: AdminConversation[] }>(
            "/admin/conversations",
          ),
        ]);
        if (!active) return;
        setStats(s);
        setUsers(u.users);
        setAppts(a.appointments);
        setConversations(c.conversations);
        setError(null);
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [statusFilter, refreshKey]);

  return (
    <>
      <Header />
      <main className="ambient flex flex-1 flex-col px-6 py-12">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-10">
          <header className="flex flex-wrap items-end justify-between gap-4">
            <div className="flex flex-col gap-2">
              <span className="inline-flex items-center gap-2 self-start rounded-full border border-(--border) bg-(--surface) px-3 py-1 text-xs font-medium text-(--muted-strong)">
                <span className="size-1.5 rounded-full bg-(--accent)" />
                Operations
              </span>
              <h1 className="text-balance text-4xl font-semibold tracking-tight sm:text-5xl">
                Dashboard
              </h1>
              <p className="text-pretty text-(--muted-strong)">
                Every call captured by Cadence — summary, intent, cost, and
                transcript.
              </p>
            </div>
            <button
              onClick={refresh}
              disabled={loading}
              className="inline-flex h-10 items-center gap-2 rounded-full border border-(--border) bg-(--surface) px-4 text-sm font-medium text-foreground transition-colors hover:bg-foreground/5 disabled:opacity-50"
            >
              <RefreshIcon spinning={loading} />
              Refresh
            </button>
          </header>

          <AnimatePresence>
            {error ? (
              <motion.div
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                className="rounded-2xl border border-(--accent) bg-(--accent-soft) px-4 py-3 text-sm"
              >
                {error}
              </motion.div>
            ) : null}
          </AnimatePresence>

          <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard
              eyebrow="Calls"
              label="Conversations"
              value={fmtCount(stats?.conversations)}
              accent
            />
            <StatCard
              eyebrow="People"
              label="Users"
              value={fmtCount(stats?.users)}
            />
            <StatCard
              eyebrow="Bookings"
              label="Booked"
              value={fmtCount(stats?.booked)}
              hint={`${fmtCount(stats?.upcoming)} upcoming`}
            />
            <StatCard
              eyebrow="Cost"
              label="Total"
              value={fmtCost(stats?.totalCostCents)}
              hint={`avg ${fmtCost(stats?.avgCostCents)} / call`}
            />
          </section>

          <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <SegmentedTabs
                tabs={[
                  { key: "conversations", label: "Conversations" },
                  { key: "appointments", label: "Appointments" },
                  { key: "users", label: "Users" },
                ]}
                active={tab}
                onChange={(t) => setTab(t as Tab)}
              />
              {tab === "appointments" ? (
                <select
                  value={statusFilter}
                  onChange={(e) =>
                    setStatusFilter(e.target.value as StatusFilter)
                  }
                  className="h-9 rounded-full border border-(--border) bg-(--surface) px-3 text-sm outline-none transition-colors focus:border-(--foreground)"
                >
                  <option value="all">All</option>
                  <option value="booked">Booked</option>
                  <option value="cancelled">Cancelled</option>
                </select>
              ) : null}
            </div>

            {tab === "users" ? (
              <UsersTable users={users} loading={loading} />
            ) : tab === "appointments" ? (
              <AppointmentsTable appointments={appts} loading={loading} />
            ) : (
              <ConversationsList
                conversations={conversations}
                loading={loading}
              />
            )}
          </div>
        </div>
      </main>
    </>
  );
}

function StatCard({
  eyebrow,
  label,
  value,
  hint,
  accent,
}: {
  eyebrow?: string;
  label: string;
  value: string;
  hint?: string;
  accent?: boolean;
}) {
  return (
    <div className="group flex flex-col gap-2 rounded-3xl border border-(--border) bg-(--surface) p-5 transition-colors hover:border-(--border-strong)">
      {eyebrow ? (
        <span
          className={`font-mono text-[10px] uppercase tracking-[0.22em] ${accent ? "text-(--accent)" : "text-(--muted)"}`}
        >
          {eyebrow}
        </span>
      ) : null}
      <span className="text-xs font-medium text-(--muted-strong)">{label}</span>
      <span
        className={`text-3xl font-semibold tracking-tight tabular-nums ${accent ? "text-foreground" : ""}`}
      >
        {value}
      </span>
      {hint ? (
        <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-(--muted)">
          {hint}
        </span>
      ) : null}
    </div>
  );
}

function SegmentedTabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { key: T; label: string }[];
  active: T;
  onChange: (key: T) => void;
}) {
  return (
    <div className="inline-flex items-center gap-0.5 rounded-full border border-(--border) bg-(--surface) p-1 text-sm">
      {tabs.map((t) => {
        const isActive = active === t.key;
        return (
          <button
            key={t.key}
            onClick={() => onChange(t.key)}
            className={`relative rounded-full px-4 py-1.5 font-medium transition-colors ${
              isActive
                ? "text-foreground"
                : "text-(--muted-strong) hover:text-foreground"
            }`}
          >
            {isActive ? (
              <motion.span
                layoutId="admin-tab"
                transition={{ type: "spring", duration: 0.4, bounce: 0.2 }}
                className="absolute inset-0 rounded-full bg-foreground/[0.08]"
              />
            ) : null}
            <span className="relative">{t.label}</span>
          </button>
        );
      })}
    </div>
  );
}

function RefreshIcon({ spinning }: { spinning?: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={`size-4 ${spinning ? "animate-spin" : ""}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
      <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M3 21v-5h5" />
    </svg>
  );
}

function UsersTable({
  users,
  loading,
}: {
  users: AdminUser[];
  loading: boolean;
}) {
  if (loading && users.length === 0) return <SkeletonRows />;
  if (users.length === 0) return <EmptyState message="No users yet." />;
  return (
    <div className="overflow-x-auto rounded-2xl border border-(--border) bg-(--surface)">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-(--muted)">
            <Th>Phone</Th>
            <Th>Name</Th>
            <Th>Joined</Th>
            <Th align="right">Active</Th>
            <Th align="right">Total</Th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id} className="border-t border-(--border)">
              <Td className="font-mono">{u.phone}</Td>
              <Td>{u.name ?? <span className="text-(--muted)">—</span>}</Td>
              <Td className="text-(--muted-strong)">
                {formatDate(u.createdAt)}
              </Td>
              <Td align="right" className="font-medium">
                {u.bookedCount}
              </Td>
              <Td align="right" className="text-(--muted-strong)">
                {u.appointmentCount}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AppointmentsTable({
  appointments,
  loading,
}: {
  appointments: AdminAppointment[];
  loading: boolean;
}) {
  if (loading && appointments.length === 0) return <SkeletonRows />;
  if (appointments.length === 0) {
    return <EmptyState message="No appointments." />;
  }
  return (
    <div className="overflow-x-auto rounded-2xl border border-(--border) bg-(--surface)">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-(--muted)">
            <Th>When</Th>
            <Th>Patient</Th>
            <Th>Phone</Th>
            <Th>Status</Th>
            <Th>Created</Th>
          </tr>
        </thead>
        <tbody>
          {appointments.map((a) => (
            <tr key={a.id} className="border-t border-(--border)">
              <Td>{formatSlot(a.startsAt)}</Td>
              <Td>
                {a.user.name ?? <span className="text-(--muted)">—</span>}
              </Td>
              <Td className="font-mono text-xs">{a.user.phone}</Td>
              <Td>
                <span
                  className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                    a.status === "booked"
                      ? "bg-foreground/8 text-foreground"
                      : "bg-[color-mix(in_oklab,_var(--accent)_18%,_transparent)] text-(--accent)"
                  }`}
                >
                  {a.status}
                </span>
              </Td>
              <Td className="text-(--muted-strong)">
                {formatDate(a.createdAt)}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ConversationsList({
  conversations,
  loading,
}: {
  conversations: AdminConversation[];
  loading: boolean;
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  if (loading && conversations.length === 0) return <SkeletonRows />;
  if (conversations.length === 0) {
    return <EmptyState message="No conversations yet." />;
  }
  return (
    <ul className="flex flex-col gap-3">
      {conversations.map((c) => {
        const isOpen = expandedId === c.id;
        return (
          <li
            key={c.id}
            className="overflow-hidden rounded-2xl border border-(--border) bg-(--surface)"
          >
            <button
              onClick={() => setExpandedId(isOpen ? null : c.id)}
              className="flex w-full flex-col gap-3 px-5 py-4 text-left transition-colors hover:bg-foreground/[0.02]"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-col gap-0.5">
                  <span className="text-sm font-medium">
                    {c.user?.name ?? (
                      <span className="text-(--muted)">Unknown caller</span>
                    )}
                  </span>
                  <span className="font-mono text-xs text-(--muted-strong)">
                    {c.user?.phone ?? "—"}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <IntentPill intent={c.intent} />
                  {c.costCents !== null ? (
                    <span
                      className="rounded-full border border-(--border) px-2 py-0.5 font-mono text-[11px] text-(--muted-strong)"
                      title="Approximate cost based on usage and current model rates"
                    >
                      {fmtCost(c.costCents)}
                    </span>
                  ) : null}
                  <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-(--muted)">
                    {formatDate(c.endedAt)}
                  </span>
                </div>
              </div>
              {c.summary ? (
                <p className="text-sm leading-relaxed text-(--muted-strong)">
                  {c.summary}
                </p>
              ) : (
                <p className="text-sm italic text-(--muted)">No summary.</p>
              )}
              {c.preferences && c.preferences.length > 0 ? (
                <ul className="flex flex-wrap gap-1.5">
                  {c.preferences.map((p, i) => (
                    <li
                      key={i}
                      className="rounded-full border border-(--border) px-2.5 py-0.5 text-[11px] text-(--muted-strong)"
                    >
                      {p}
                    </li>
                  ))}
                </ul>
              ) : null}
            </button>
            {isOpen && c.costBreakdown ? (
              <div className="border-t border-(--border) bg-(--background) px-5 py-4">
                <h4 className="mb-3 font-mono text-[11px] uppercase tracking-[0.2em] text-(--muted)">
                  Cost breakdown
                </h4>
                <CostBreakdownRow breakdown={c.costBreakdown} />
              </div>
            ) : null}
            {isOpen && c.transcript && c.transcript.length > 0 ? (
              <div className="border-t border-(--border) bg-(--background) px-5 py-4">
                <h4 className="mb-3 font-mono text-[11px] uppercase tracking-[0.2em] text-(--muted)">
                  Transcript
                </h4>
                <ul className="flex flex-col gap-2">
                  {c.transcript.map((t, i) => (
                    <li
                      key={i}
                      className={
                        t.role === "user"
                          ? "flex justify-end"
                          : "flex justify-start"
                      }
                    >
                      <div
                        className={
                          t.role === "user"
                            ? "max-w-[85%] rounded-2xl rounded-tr-sm bg-foreground px-3 py-2 text-xs text-background"
                            : "max-w-[85%] rounded-2xl rounded-tl-sm border border-(--border) px-3 py-2 text-xs text-foreground"
                        }
                      >
                        <span
                          className={
                            t.role === "user"
                              ? "block font-mono text-[9px] uppercase tracking-[0.18em] text-background/60"
                              : "block font-mono text-[9px] uppercase tracking-[0.18em] text-(--muted)"
                          }
                        >
                          {t.role === "user" ? "Caller" : "Cadence"}
                        </span>
                        <p className="mt-0.5 whitespace-pre-wrap leading-snug">
                          {t.text}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function IntentPill({ intent }: { intent: string | null }) {
  const label = intent ? INTENT_LABELS[intent] ?? intent : "—";
  const tone =
    intent === "book_appointment"
      ? "border-(--border) bg-foreground/8 text-foreground"
      : intent === "cancel_appointment"
        ? "border-(--accent) bg-[color-mix(in_oklab,_var(--accent)_12%,_transparent)] text-(--accent)"
        : "border-(--border) bg-(--background) text-(--muted-strong)";
  return (
    <span
      className={`inline-flex rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${tone}`}
    >
      {label}
    </span>
  );
}

const INTENT_LABELS: Record<string, string> = {
  book_appointment: "Booking",
  cancel_appointment: "Cancellation",
  modify_appointment: "Reschedule",
  retrieve_appointments: "Lookup",
  general_inquiry: "Inquiry",
  other: "Other",
  unknown: "Unknown",
};

function Th({
  children,
  align,
}: {
  children: React.ReactNode;
  align?: "right" | "left";
}) {
  return (
    <th
      className={`px-4 py-3 font-medium ${align === "right" ? "text-right" : ""}`}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  className = "",
  align,
}: {
  children: React.ReactNode;
  className?: string;
  align?: "right" | "left";
}) {
  return (
    <td
      className={`px-4 py-3 ${align === "right" ? "text-right" : ""} ${className}`}
    >
      {children}
    </td>
  );
}

function SkeletonRows() {
  return (
    <div className="flex flex-col gap-2">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="h-10 animate-pulse rounded-xl bg-foreground/5"
        />
      ))}
    </div>
  );
}

function CostBreakdownRow({ breakdown }: { breakdown: CostBreakdown }) {
  const rows: Array<{ label: string; detail: string; cents: number }> = [];
  if (breakdown.llm) {
    rows.push({
      label: "LLM",
      detail: `${breakdown.llm.inputTokens.toLocaleString()} in · ${breakdown.llm.outputTokens.toLocaleString()} out`,
      cents: breakdown.llm.costCents,
    });
  }
  if (breakdown.stt) {
    rows.push({
      label: "STT",
      detail: `${breakdown.stt.audioSeconds.toFixed(1)}s audio`,
      cents: breakdown.stt.costCents,
    });
  }
  if (breakdown.tts) {
    rows.push({
      label: "TTS",
      detail: `${breakdown.tts.characters.toLocaleString()} chars`,
      cents: breakdown.tts.costCents,
    });
  }
  if (breakdown.avatar) {
    rows.push({
      label: "Avatar",
      detail: `${breakdown.avatar.audioSeconds.toFixed(1)}s`,
      cents: breakdown.avatar.costCents,
    });
  }
  if (rows.length === 0) {
    return (
      <p className="text-xs italic text-(--muted)">
        No usage recorded for this call.
      </p>
    );
  }
  return (
    <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {rows.map((r) => (
        <li
          key={r.label}
          className="flex flex-col gap-0.5 rounded-xl border border-(--border) bg-(--surface) px-3 py-2"
        >
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-(--muted)">
            {r.label}
          </span>
          <span className="text-sm font-medium">{fmtCost(r.cents)}</span>
          <span className="text-[11px] text-(--muted-strong)">{r.detail}</span>
        </li>
      ))}
    </ul>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-2xl border border-(--border) bg-(--surface) p-8 text-center text-sm text-(--muted-strong)">
      {message}
    </div>
  );
}

function fmtCount(n: number | undefined): string {
  if (n === undefined || n === null) return "—";
  return new Intl.NumberFormat().format(n);
}

function fmtCost(cents: number | null | undefined): string {
  if (cents === undefined || cents === null) return "—";
  // Show sub-cent precision for small numbers so a 2¢ call doesn't read as $0.
  const dollars = cents / 100;
  return dollars >= 1
    ? `$${dollars.toFixed(2)}`
    : `${cents.toLocaleString()}¢`;
}

function formatSlot(iso: string): string {
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(iso));
}

function formatDate(iso: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(iso));
}
