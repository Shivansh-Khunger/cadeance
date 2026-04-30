import Link from "next/link";

import { Header } from "./_components/Header";

const FEATURES = [
  {
    eyebrow: "Real-time",
    title: "Sub-second voice",
    body: "Streaming STT, LLM, and TTS through LiveKit means Cadence answers like a person, not a chatbot.",
  },
  {
    eyebrow: "Memory",
    title: "Recalls past calls",
    body: "Returning callers are recognized by phone — Cadence pulls in summaries, intents, and preferences automatically.",
  },
  {
    eyebrow: "Telemetry",
    title: "Live cost & intent",
    body: "Every call is summarized, classified, and priced. The dashboard shows what happened and what it cost.",
  },
];

export default function Home() {
  return (
    <>
      <Header />
      <main className="ambient relative flex flex-1 flex-col">
        <section className="flex flex-1 flex-col items-center justify-center px-6 pb-20 pt-24 text-center">
          <span className="inline-flex items-center gap-2 rounded-full border border-(--border) bg-(--surface) px-3 py-1 text-xs font-medium text-(--muted-strong) shadow-sm">
            <span className="size-1.5 animate-pulse rounded-full bg-(--accent)" />
            Voice agent · live
          </span>
          <h1 className="mt-6 text-balance bg-gradient-to-b from-foreground to-foreground/70 bg-clip-text text-5xl font-semibold tracking-tight text-transparent sm:text-7xl">
            The front desk,
            <br className="hidden sm:block" />
            on the line.
          </h1>
          <p className="mt-6 max-w-xl text-pretty text-base text-(--muted-strong) sm:text-lg">
            Cadence books, reschedules, and cancels appointments by voice — and
            remembers every caller from one call to the next.
          </p>
          <div className="mt-10 flex flex-col items-center gap-3 sm:flex-row">
            <Link
              href="/agent"
              className="group relative inline-flex h-12 items-center justify-center gap-2 overflow-hidden rounded-full bg-foreground px-8 text-base font-medium text-background transition-transform hover:scale-[1.02]"
            >
              <span className="relative">Start a conversation</span>
              <ArrowIcon />
            </Link>
            <Link
              href="/admin"
              className="inline-flex h-12 items-center justify-center rounded-full border border-(--border) bg-(--surface) px-8 text-base font-medium text-foreground transition-colors hover:bg-foreground/5"
            >
              View dashboard
            </Link>
          </div>
        </section>

        <section className="px-6 pb-24">
          <div className="mx-auto grid w-full max-w-5xl gap-3 sm:grid-cols-3">
            {FEATURES.map((f) => (
              <article
                key={f.title}
                className="flex flex-col gap-3 rounded-3xl border border-(--border) bg-(--surface) p-6 transition-colors hover:border-(--border-strong)"
              >
                <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-(--accent)">
                  {f.eyebrow}
                </span>
                <h3 className="text-lg font-semibold tracking-tight">
                  {f.title}
                </h3>
                <p className="text-sm leading-relaxed text-(--muted-strong)">
                  {f.body}
                </p>
              </article>
            ))}
          </div>
        </section>

        <footer className="px-6 pb-10">
          <div className="mx-auto flex w-full max-w-5xl items-center justify-between border-t border-(--border) pt-6">
            <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-(--muted)">
              Powered by LiveKit
            </span>
            <a
              href="https://docs.livekit.io/agents/"
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono text-[10px] uppercase tracking-[0.22em] text-(--muted) transition-colors hover:text-foreground"
            >
              How it works ↗
            </a>
          </div>
        </footer>
      </main>
    </>
  );
}

function ArrowIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-4 transition-transform group-hover:translate-x-0.5"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M5 12h14M13 5l7 7-7 7" />
    </svg>
  );
}
