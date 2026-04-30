"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  BarVisualizer,
  LiveKitRoom,
  RoomAudioRenderer,
  VideoTrack,
  useLocalParticipant,
  useRoomContext,
  useTrackToggle,
  useTranscriptions,
  useVoiceAssistant,
} from "@livekit/components-react";
import { useKrispNoiseFilter } from "@livekit/components-react/krisp";
import "@livekit/components-styles";
import { Track } from "livekit-client";
import { AnimatePresence, motion } from "motion/react";
import type { TextStreamData } from "@livekit/components-react";

import { Header } from "../_components/Header";

type ConnectionDetails = {
  server_url: string;
  participant_token: string;
  room_name: string;
  participant_name: string;
};

type ToolStatus = "running" | "success" | "error";

type ToolEvent = {
  id: string;
  name: string;
  status: ToolStatus;
  label: string;
  detail?: string | null;
  ts: number;
};

type SummaryAppointment = {
  id: string;
  startsAt: string;
  status: "booked" | "cancelled";
  notes: string | null;
  createdAt: string;
};

type SummaryUser = {
  id: string;
  phone: string;
  name: string | null;
};

type Summary = {
  conversation: string;
  preferences: string[];
  appointments: SummaryAppointment[];
  user: SummaryUser | null;
  timestamp: string;
};

const TOOL_TOPIC = "cadence.tool";
const SUMMARY_TOPIC = "cadence.summary";

export default function AgentPage() {
  const [details, setDetails] = useState<ConnectionDetails | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [finishing, setFinishing] = useState(false);
  const router = useRouter();
  const summaryRef = useRef<Summary | null>(null);
  useEffect(() => {
    summaryRef.current = summary;
  }, [summary]);

  async function start() {
    setLoading(true);
    setError(null);
    setSummary(null);
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL}/connection-details`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        },
      );
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }
      setDetails(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to connect");
    } finally {
      setLoading(false);
    }
  }

  const handleSummary = useCallback((s: Summary) => {
    setSummary(s);
  }, []);

  const handleDisconnected = useCallback(async () => {
    const roomName = details?.room_name;
    setDetails(null);
    // If `end_conversation` already published the summary topic, the modal is
    // already showing — nothing to do.
    if (summaryRef.current || !roomName) return;
    // Otherwise the agent's shutdown hook is still saving the call. Poll the
    // API until the row appears, then rebuild the modal from it.
    setFinishing(true);
    try {
      for (let attempt = 0; attempt < 15; attempt++) {
        await new Promise((r) => setTimeout(r, 1500));
        try {
          const res = await fetch(
            `${process.env.NEXT_PUBLIC_API_URL}/conversations/by-room/${encodeURIComponent(
              roomName,
            )}`,
          );
          if (!res.ok) continue;
          const data = (await res.json()) as {
            conversation: {
              summary: string | null;
              preferences: string[] | null;
              endedAt: string;
              user: { id: string; phone: string; name: string | null } | null;
            };
            appointments: SummaryAppointment[];
          };
          setSummary({
            conversation:
              data.conversation.summary ??
              "Call ended without a written summary.",
            preferences: data.conversation.preferences ?? [],
            appointments: data.appointments ?? [],
            user: data.conversation.user ?? null,
            timestamp: data.conversation.endedAt,
          });
          return;
        } catch {
          // network blip — try again next tick
        }
      }
    } finally {
      setFinishing(false);
    }
  }, [details]);

  return (
    <>
      <Header compact />
      <main className="ambient relative flex flex-1 flex-col items-center justify-center px-6 py-10">
        <AnimatePresence mode="wait">
          {!details ? (
            <motion.div
              key="idle"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.25 }}
              className="flex w-full max-w-md flex-col items-center text-center"
            >
              <div className="relative">
                <div className="orb-pulse pointer-events-none size-44 rounded-full bg-[radial-gradient(circle_at_50%_40%,_color-mix(in_oklab,_var(--accent)_28%,_transparent),_transparent_70%)]" />
                <div className="orb-pulse pointer-events-none absolute inset-0 size-44 rounded-full bg-[radial-gradient(circle_at_50%_40%,_color-mix(in_oklab,_var(--foreground)_18%,_transparent),_transparent_70%)] [animation-delay:1.5s]" />
              </div>
              <h1 className="-mt-2 text-balance text-4xl font-semibold tracking-tight sm:text-5xl">
                Ready when you are
              </h1>
              <p className="mt-3 max-w-sm text-pretty text-(--muted-strong)">
                Allow your microphone, then talk to Cadence like you&apos;d
                talk to the front desk.
              </p>
              <button
                onClick={start}
                disabled={loading}
                className="mt-8 inline-flex h-12 w-full items-center justify-center gap-2 rounded-full bg-foreground px-8 text-base font-medium text-background transition-transform hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {loading ? (
                  <>
                    <span className="size-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                    Connecting…
                  </>
                ) : (
                  <>
                    <MicIcon />
                    Start conversation
                  </>
                )}
              </button>
              <AnimatePresence>
                {error ? (
                  <motion.p
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    exit={{ opacity: 0, height: 0 }}
                    className="mt-4 text-sm text-(--accent)"
                  >
                    {error}
                  </motion.p>
                ) : null}
              </AnimatePresence>
              <p className="mt-10 max-w-sm text-xs text-(--muted)">
                Tip: try &ldquo;Book me something tomorrow afternoon&rdquo; or
                &ldquo;Cancel my Tuesday appointment&rdquo;.
              </p>
            </motion.div>
          ) : (
            <motion.div
              key="active"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.3 }}
              className="flex w-full flex-1 flex-col"
            >
              <LiveKitRoom
                serverUrl={details.server_url}
                token={details.participant_token}
                connect
                audio
                onDisconnected={() => void handleDisconnected()}
                data-lk-theme="default"
                className="flex flex-1 flex-col"
              >
                <RoomAudioRenderer />
                <ActiveSession
                  roomName={details.room_name}
                  onSummary={handleSummary}
                />
              </LiveKitRoom>
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence>
          {summary ? (
            <SummaryModal
              summary={summary}
              onStartOver={() => {
                setSummary(null);
                setDetails(null);
              }}
              onClose={() => router.push("/")}
              onViewDashboard={() => router.push("/admin")}
            />
          ) : finishing ? (
            <FinishingOverlay />
          ) : null}
        </AnimatePresence>
      </main>
    </>
  );
}

function FinishingOverlay() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.25 }}
      className="fixed inset-0 z-50 flex items-center justify-center px-4 backdrop-blur-md"
      style={{
        backgroundColor:
          "color-mix(in oklab, var(--foreground) 30%, transparent)",
      }}
    >
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: 8 }}
        className="flex flex-col items-center gap-4 rounded-3xl border border-(--border) bg-(--surface) px-8 py-7 text-center shadow-[0_30px_80px_-30px_rgba(0,0,0,0.5)]"
      >
        <span className="size-8 animate-spin rounded-full border-2 border-(--muted-strong) border-t-transparent" />
        <div className="flex flex-col gap-1">
          <p className="text-base font-medium tracking-tight">
            Wrapping up your call
          </p>
          <p className="text-sm text-(--muted-strong)">
            Saving the transcript and summary…
          </p>
        </div>
      </motion.div>
    </motion.div>
  );
}

function MicIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
      <path d="M19 10a7 7 0 0 1-14 0" />
      <path d="M12 19v3" />
    </svg>
  );
}

function ActiveSession({
  roomName,
  onSummary,
}: {
  roomName: string;
  onSummary: (s: Summary) => void;
}) {
  const { state, audioTrack, videoTrack } = useVoiceAssistant();
  const status = state ?? "connecting";
  const isSpeaking = status === "speaking";

  return (
    <div className="flex flex-1 flex-col items-center justify-between gap-8 py-6">
      <header className="flex w-full max-w-6xl items-center justify-between text-xs">
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center gap-2 rounded-full border border-(--border) bg-(--surface) px-3 py-1.5 font-mono uppercase tracking-[0.18em] text-(--muted-strong)">
            <span className="size-1.5 animate-pulse rounded-full bg-(--success)" />
            Live
          </span>
          <span className="hidden font-mono uppercase tracking-[0.18em] text-(--muted) sm:inline">
            {roomName.replace(/^room-/, "").slice(0, 12)}
          </span>
          <NoiseFilter />
        </div>
      </header>

      <div className="grid w-full max-w-6xl flex-1 grid-cols-1 items-stretch gap-6 md:grid-cols-[minmax(0,1fr)_420px]">
        <div className="flex flex-col items-center justify-center gap-6">
          <AnimatePresence mode="wait">
            {videoTrack ? (
              <motion.div
                key="video"
                initial={{ opacity: 0, scale: 0.96 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.96 }}
                transition={{ duration: 0.3 }}
                className={`relative aspect-square w-full max-w-md overflow-hidden rounded-[2rem] border border-(--border) bg-(--surface) shadow-[0_40px_100px_-40px_rgba(0,0,0,0.55)] transition-shadow ${
                  isSpeaking ? "glow-ring-active" : ""
                }`}
              >
                <VideoTrack
                  trackRef={videoTrack}
                  className="h-full w-full object-cover"
                />
                <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center justify-center gap-2 bg-gradient-to-t from-black/45 via-black/15 to-transparent p-4">
                  <BarVisualizer
                    state={state}
                    barCount={5}
                    trackRef={audioTrack}
                    options={{ minHeight: 6, maxHeight: 30 }}
                    className="h-8 w-32 [&_.lk-audio-bar]:!bg-white/60 [&_.lk-audio-bar.lk-highlighted]:!bg-white"
                  />
                </div>
              </motion.div>
            ) : (
              <motion.div
                key="waiting"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.3 }}
                className="flex h-64 w-full max-w-xl flex-col items-center justify-center gap-5"
              >
                <BarVisualizer
                  state={state}
                  barCount={9}
                  trackRef={audioTrack}
                  options={{ minHeight: 8, maxHeight: 90 }}
                  className="h-40 w-full"
                />
                <p className="text-base text-(--muted-strong) sm:text-lg">
                  Agent will join shortly…
                </p>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <Conversation onSummary={onSummary} />
      </div>

      <IconControlBar />
    </div>
  );
}

function IconControlBar() {
  const room = useRoomContext();
  const { toggle: toggleMic, enabled: micOn } = useTrackToggle({
    source: Track.Source.Microphone,
  });

  return (
    <div className="flex items-center gap-1.5 rounded-full border border-(--border) bg-(--surface) p-1.5 shadow-[0_20px_60px_-30px_rgba(0,0,0,0.4)]">
      <button
        type="button"
        onClick={() => void toggleMic()}
        className={`inline-flex size-11 items-center justify-center rounded-full transition-colors ${
          micOn
            ? "bg-foreground/[0.06] text-foreground hover:bg-foreground/[0.12]"
            : "bg-(--accent-soft) text-(--accent)"
        }`}
        aria-label={micOn ? "Mute microphone" : "Unmute microphone"}
        title={micOn ? "Mute microphone" : "Unmute microphone"}
      >
        {micOn ? <MicIcon /> : <MicOffIcon />}
      </button>
      <button
        type="button"
        onClick={() => void room.disconnect()}
        className="inline-flex h-11 items-center justify-center rounded-full bg-red-500 px-6 text-white shadow-[0_8px_20px_-8px_rgba(239,68,68,0.6)] transition-colors hover:bg-red-600"
        aria-label="End call"
        title="End call"
      >
        <HangupIcon />
      </button>
    </div>
  );
}

function MicOffIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <line x1="2" y1="2" x2="22" y2="22" />
      <path d="M18.89 13.23A7.12 7.12 0 0 0 19 12v-2" />
      <path d="M5 10v2a7 7 0 0 0 12 5" />
      <path d="M15 9.34V5a3 3 0 0 0-5.68-1.33" />
      <path d="M9 9v3a3 3 0 0 0 5.12 2.12" />
      <line x1="12" y1="19" x2="12" y2="22" />
    </svg>
  );
}

function HangupIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-4"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M10.68 13.31a16 16 0 0 0 3.41 2.6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7 2 2 0 0 1 1.72 2v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.42 19.42 0 0 1-3.33-2.67m-2.67-3.34a19.79 19.79 0 0 1-3.07-8.63A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91" />
      <line x1="23" y1="1" x2="1" y2="23" />
    </svg>
  );
}

function NoiseFilter() {
  const { setNoiseFilterEnabled, isNoiseFilterEnabled, isNoiseFilterPending } =
    useKrispNoiseFilter();

  useEffect(() => {
    if (!isNoiseFilterEnabled && !isNoiseFilterPending) {
      setNoiseFilterEnabled(true).catch(() => {});
    }
  }, [setNoiseFilterEnabled, isNoiseFilterEnabled, isNoiseFilterPending]);

  if (!isNoiseFilterEnabled && !isNoiseFilterPending) {
    return null;
  }

  return (
    <span className="inline-flex items-center gap-2 font-mono uppercase tracking-[0.2em] text-(--muted)">
      <span
        className={`size-1.5 rounded-full ${
          isNoiseFilterPending ? "bg-(--accent) animate-pulse" : "bg-emerald-500"
        }`}
      />
      NC
    </span>
  );
}

type Message = {
  id: string;
  text: string;
  isUser: boolean;
  timestamp: number;
};

// Sync transcripts arrive paced with the agent's TTS, but the Beyond Presence
// avatar adds latency before the audio actually plays — so transcripts land in
// the UI before the user hears anything. Hold each agent text update for ~1.5s
// before applying so the bubble tracks the avatar's playback instead.
const AVATAR_TRANSCRIPT_DELAY_MS = 1500;

type Slot = { applyAt: number; message: Message };
type AlignedState = {
  pending: Map<string, Slot[]>;
  applied: Map<string, Message>;
  seen: Map<string, string>;
};

function useAvatarAlignedMessages(
  rawMessages: Message[],
  delayMs: number,
): Message[] {
  const [state, setState] = useState<AlignedState>(() => ({
    pending: new Map(),
    applied: new Map(),
    seen: new Map(),
  }));

  useEffect(() => {
    const now = Date.now();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- we're syncing prop into derived state with time-anchored slots
    setState((prev) => {
      const pending = new Map<string, Slot[]>();
      for (const [id, q] of prev.pending) pending.set(id, [...q]);
      const applied = new Map(prev.applied);
      const seen = new Map(prev.seen);
      let changed = false;

      for (const m of rawMessages) {
        if (seen.get(m.id) === m.text) continue;
        seen.set(m.id, m.text);
        changed = true;
        if (m.isUser) {
          applied.set(m.id, m);
        } else {
          const q = pending.get(m.id) ?? [];
          q.push({ applyAt: now + delayMs, message: m });
          pending.set(m.id, q);
        }
      }

      return changed ? { pending, applied, seen } : prev;
    });
  }, [rawMessages, delayMs]);

  useEffect(() => {
    let nextDeadline = Number.POSITIVE_INFINITY;
    for (const q of state.pending.values()) {
      if (q.length > 0) nextDeadline = Math.min(nextDeadline, q[0].applyAt);
    }
    if (!Number.isFinite(nextDeadline)) return;
    const wait = Math.max(0, nextDeadline - Date.now()) + 30;
    const timer = setTimeout(() => {
      const now = Date.now();
      setState((prev) => {
        const pending = new Map<string, Slot[]>();
        for (const [id, q] of prev.pending) pending.set(id, [...q]);
        const applied = new Map(prev.applied);
        let changed = false;
        for (const [id, q] of pending) {
          while (q.length > 0 && q[0].applyAt <= now) {
            applied.set(id, q.shift()!.message);
            changed = true;
          }
          if (q.length === 0) {
            pending.delete(id);
            changed = true;
          }
        }
        return changed ? { ...prev, pending, applied } : prev;
      });
    }, wait);
    return () => clearTimeout(timer);
  }, [state.pending]);

  return useMemo(
    () =>
      Array.from(state.applied.values()).sort(
        (a, b) => a.timestamp - b.timestamp,
      ),
    [state.applied],
  );
}

type TimelineItem =
  | {
      kind: "msg";
      id: string;
      role: "user" | "agent";
      text: string;
      ts: number;
    }
  | {
      kind: "tool";
      id: string;
      name: string;
      status: ToolStatus;
      label: string;
      ts: number;
    };

function Conversation({ onSummary }: { onSummary: (s: Summary) => void }) {
  const room = useRoomContext();
  const transcriptions = useTranscriptions();
  const { localParticipant } = useLocalParticipant();
  const localIdentity = localParticipant?.identity;
  const [toolEvents, setToolEvents] = useState<Map<string, ToolEvent>>(
    new Map(),
  );
  const onSummaryRef = useRef(onSummary);
  useEffect(() => {
    onSummaryRef.current = onSummary;
  }, [onSummary]);

  // Subscribe to tool-event text streams.
  useEffect(() => {
    const handler = (reader: { readAll: () => Promise<string> }) => {
      void (async () => {
        try {
          const raw = await reader.readAll();
          const evt = JSON.parse(raw) as ToolEvent;
          if (!evt?.id || !evt?.status) return;
          setToolEvents((prev) => {
            const next = new Map(prev);
            next.set(evt.id, evt);
            return next;
          });
        } catch {
          // malformed payload — ignore
        }
      })();
    };
    room.registerTextStreamHandler(TOOL_TOPIC, handler);
    return () => {
      room.unregisterTextStreamHandler(TOOL_TOPIC);
    };
  }, [room]);

  // Subscribe to summary text stream and lift to parent.
  useEffect(() => {
    const handler = (reader: { readAll: () => Promise<string> }) => {
      void (async () => {
        try {
          const raw = await reader.readAll();
          const summary = JSON.parse(raw) as Summary;
          if (summary?.conversation !== undefined) {
            onSummaryRef.current(summary);
          }
        } catch {
          // ignore
        }
      })();
    };
    room.registerTextStreamHandler(SUMMARY_TOPIC, handler);
    return () => {
      room.unregisterTextStreamHandler(SUMMARY_TOPIC);
    };
  }, [room]);

  // Safety net: if a tool event sits in `running` for over 30s with no
  // terminal event, demote it to `error` so the spinner stops. Backend
  // timeout is 8s; this catches the rare case where the terminal publish
  // itself fails (e.g. agent crash, network drop after the request).
  useEffect(() => {
    const interval = setInterval(() => {
      setToolEvents((prev) => {
        const now = Date.now();
        let mutated = false;
        const next = new Map(prev);
        for (const [id, evt] of next) {
          if (evt.status === "running" && now - evt.ts > 30_000) {
            next.set(id, {
              ...evt,
              status: "error",
              label: `${evt.label} (timed out)`,
            });
            mutated = true;
          }
        }
        return mutated ? next : prev;
      });
    }, 5_000);
    return () => clearInterval(interval);
  }, []);

  const rawMessages = useMemo<Message[]>(() => {
    const bySegment = new Map<string, TextStreamData>();
    for (const entry of transcriptions) {
      const segmentId =
        entry.streamInfo.attributes?.["lk.segment_id"] ?? entry.streamInfo.id;
      bySegment.set(segmentId, entry);
    }
    return Array.from(bySegment.entries())
      .map(([id, entry]) => ({
        id,
        text: entry.text,
        isUser: entry.participantInfo.identity === localIdentity,
        timestamp: entry.streamInfo.timestamp,
      }))
      .sort((a, b) => a.timestamp - b.timestamp);
  }, [transcriptions, localIdentity]);

  const messages = useAvatarAlignedMessages(
    rawMessages,
    AVATAR_TRANSCRIPT_DELAY_MS,
  );

  const timeline = useMemo<TimelineItem[]>(() => {
    const items: TimelineItem[] = [];
    for (const m of messages) {
      items.push({
        kind: "msg",
        id: m.id,
        role: m.isUser ? "user" : "agent",
        text: m.text,
        ts: m.timestamp,
      });
    }
    for (const evt of toolEvents.values()) {
      items.push({
        kind: "tool",
        id: evt.id,
        name: evt.name,
        status: evt.status,
        label: evt.label,
        ts: evt.ts,
      });
    }
    items.sort((a, b) => a.ts - b.ts);
    return items;
  }, [messages, toolEvents]);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [timeline]);

  return (
    <aside className="flex h-[28rem] flex-col overflow-hidden rounded-[2rem] border border-(--border) bg-(--surface) shadow-[0_30px_80px_-40px_rgba(0,0,0,0.4)] md:h-auto md:max-h-[34rem]">
      <header className="flex items-center justify-between border-b border-(--border) bg-(--surface-2) px-5 py-3">
        <div className="flex items-center gap-2">
          <ChatIcon />
          <h2 className="text-sm font-medium tracking-tight">Conversation</h2>
        </div>
        <span className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.18em] text-(--muted)">
          <span className="size-1 animate-pulse rounded-full bg-(--success)" />
          Live
        </span>
      </header>
      <div
        ref={scrollRef}
        className="scroll-quiet flex-1 overflow-y-auto px-5 py-4"
      >
        {timeline.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <ChatIcon className="size-6 text-(--muted)" />
            <p className="text-sm text-(--muted-strong)">
              The conversation will appear here as you talk.
            </p>
          </div>
        ) : (
          <ul className="flex flex-col gap-3">
            <AnimatePresence initial={false}>
              {timeline.map((item) =>
                item.kind === "msg" ? (
                  <motion.li
                    key={`m-${item.id}`}
                    layout
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    className={
                      item.role === "user"
                        ? "flex justify-end"
                        : "flex justify-start"
                    }
                  >
                    <div
                      className={
                        item.role === "user"
                          ? "max-w-[85%] rounded-2xl rounded-tr-sm bg-foreground px-3.5 py-2 text-sm leading-snug text-background"
                          : "max-w-[85%] rounded-2xl rounded-tl-sm border border-(--border) px-3.5 py-2 text-sm leading-snug text-foreground"
                      }
                    >
                      <span
                        className={
                          item.role === "user"
                            ? "block font-mono text-[10px] uppercase tracking-[0.18em] text-background/60"
                            : "block font-mono text-[10px] uppercase tracking-[0.18em] text-(--muted)"
                        }
                      >
                        {item.role === "user" ? "You" : "Cadence"}
                      </span>
                      <p className="mt-1 whitespace-pre-wrap">{item.text}</p>
                    </div>
                  </motion.li>
                ) : (
                  <motion.li
                    key={`t-${item.id}`}
                    layout
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="flex justify-center"
                  >
                    <ToolPill status={item.status} label={item.label} />
                  </motion.li>
                ),
              )}
            </AnimatePresence>
          </ul>
        )}
      </div>
    </aside>
  );
}

function ToolPill({
  status,
  label,
}: {
  status: ToolStatus;
  label: string;
}) {
  const tone =
    status === "error"
      ? "border-(--accent) text-(--accent) bg-(--accent-soft)"
      : status === "success"
        ? "border-(--border) text-(--muted-strong) bg-(--surface-2)"
        : "border-(--border-strong) text-(--muted-strong) bg-(--surface-2)";
  return (
    <div
      className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium shadow-sm ${tone}`}
    >
      <StatusIcon status={status} />
      <span>{label}</span>
    </div>
  );
}

function ChatIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className ?? "size-4 text-(--muted-strong)"}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
    </svg>
  );
}

function StatusIcon({ status }: { status: ToolStatus }) {
  if (status === "running") {
    return (
      <span className="inline-block size-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
    );
  }
  if (status === "success") {
    return (
      <svg
        viewBox="0 0 24 24"
        className="size-3 text-emerald-500 dark:text-emerald-400"
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M5 13l4 4L19 7" />
      </svg>
    );
  }
  return <span className="size-1.5 rounded-full bg-current" />;
}

function SummaryModal({
  summary,
  onStartOver,
  onClose,
  onViewDashboard,
}: {
  summary: Summary;
  onStartOver: () => void;
  onClose: () => void;
  onViewDashboard: () => void;
}) {
  const formatted = useMemo(
    () =>
      new Intl.DateTimeFormat(undefined, {
        weekday: "short",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }).format(new Date(summary.timestamp)),
    [summary.timestamp],
  );

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.25 }}
      className="fixed inset-0 z-50 flex items-center justify-center px-4 py-8 backdrop-blur-md"
      style={{
        backgroundColor: "color-mix(in oklab, var(--foreground) 30%, transparent)",
      }}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 12 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 12 }}
        transition={{ duration: 0.3 }}
        className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-3xl border border-(--border) bg-(--surface) shadow-[0_50px_120px_-30px_rgba(0,0,0,0.6)]"
      >
        <header className="flex items-center justify-between border-b border-(--border) px-6 py-5">
          <span className="inline-flex items-center gap-2 rounded-full border border-(--border) px-3 py-1 text-xs font-medium text-(--muted-strong)">
            <span className="size-1.5 rounded-full bg-(--accent)" />
            Call summary
          </span>
          <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-(--muted)">
            {formatted}
          </span>
        </header>

        <div className="flex flex-col gap-6 overflow-y-auto px-6 py-6">
          <div>
            <h2 className="text-balance text-2xl font-semibold tracking-tight">
              Here&apos;s what we covered
            </h2>
            {summary.user ? (
              <p className="mt-1 text-sm text-(--muted-strong)">
                Caller: {summary.user.name ?? "—"}{" "}
                <span className="font-mono">({summary.user.phone})</span>
              </p>
            ) : null}
          </div>

          <section className="flex flex-col gap-2">
            <h3 className="font-mono text-xs uppercase tracking-[0.2em] text-(--muted)">
              Conversation
            </h3>
            <p className="text-pretty text-base leading-relaxed text-foreground">
              {summary.conversation}
            </p>
          </section>

          <section className="flex flex-col gap-2">
            <h3 className="font-mono text-xs uppercase tracking-[0.2em] text-(--muted)">
              Appointments
            </h3>
            {summary.appointments.length === 0 ? (
              <p className="text-sm text-(--muted-strong)">
                No appointments on file.
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {summary.appointments.map((a) => (
                  <li
                    key={a.id}
                    className={`flex items-center justify-between rounded-2xl border border-(--border) bg-(--background) px-4 py-3 text-sm ${
                      a.status === "cancelled" ? "opacity-60" : ""
                    }`}
                  >
                    <span
                      className={
                        a.status === "cancelled"
                          ? "line-through"
                          : "font-medium"
                      }
                    >
                      {formatAppointment(a.startsAt)}
                    </span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                        a.status === "booked"
                          ? "bg-foreground/8 text-foreground"
                          : "bg-[color-mix(in_oklab,_var(--accent)_18%,_transparent)] text-(--accent)"
                      }`}
                    >
                      {a.status}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {summary.preferences.length > 0 ? (
            <section className="flex flex-col gap-2">
              <h3 className="font-mono text-xs uppercase tracking-[0.2em] text-(--muted)">
                Preferences
              </h3>
              <ul className="flex flex-col gap-1.5 text-sm text-foreground">
                {summary.preferences.map((p, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="text-(--muted)">·</span>
                    <span>{p}</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>

        <footer className="flex flex-col gap-3 border-t border-(--border) px-6 py-5 sm:flex-row sm:justify-end">
          <button
            onClick={onStartOver}
            className="inline-flex h-11 items-center justify-center rounded-full border border-(--border) px-6 text-sm font-medium text-foreground transition-colors hover:bg-foreground/5"
          >
            Start over
          </button>
          <button
            onClick={onViewDashboard}
            className="inline-flex h-11 items-center justify-center rounded-full border border-(--border) px-6 text-sm font-medium text-foreground transition-colors hover:bg-foreground/5"
          >
            View dashboard
          </button>
          <button
            onClick={onClose}
            className="inline-flex h-11 items-center justify-center rounded-full bg-foreground px-6 text-sm font-medium text-background transition-colors hover:bg-foreground/85"
          >
            Close
          </button>
        </footer>
      </motion.div>
    </motion.div>
  );
}

function formatAppointment(iso: string): string {
  return new Intl.DateTimeFormat(undefined, {
    weekday: "long",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(iso));
}
