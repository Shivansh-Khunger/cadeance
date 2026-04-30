"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV: { href: string; label: string }[] = [
  { href: "/agent", label: "Talk" },
  { href: "/admin", label: "Dashboard" },
];

export function Header({ compact = false }: { compact?: boolean }) {
  const pathname = usePathname() ?? "/";
  return (
    <header
      className={`sticky top-0 z-30 flex w-full items-center justify-between border-b border-(--border) bg-[color-mix(in_oklab,_var(--background)_82%,_transparent)] px-6 backdrop-blur-md ${
        compact ? "py-2.5" : "py-3.5"
      }`}
    >
      <Link href="/" className="group flex items-center gap-2.5">
        <span className="relative flex size-2.5 items-center justify-center">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-(--accent) opacity-50" />
          <span className="relative inline-flex size-2.5 rounded-full bg-(--accent)" />
        </span>
        <span className="text-sm font-semibold tracking-tight">Cadence</span>
        <span className="hidden font-mono text-[10px] uppercase tracking-[0.18em] text-(--muted) sm:inline">
          / front-desk
        </span>
      </Link>
      <nav className="flex items-center gap-1">
        {NAV.map((item) => {
          const active =
            pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`relative rounded-full px-4 py-1.5 text-sm font-medium transition-colors ${
                active
                  ? "text-foreground"
                  : "text-(--muted-strong) hover:text-foreground"
              }`}
            >
              {active ? (
                <span className="absolute inset-0 rounded-full bg-foreground/[0.06]" />
              ) : null}
              <span className="relative">{item.label}</span>
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
