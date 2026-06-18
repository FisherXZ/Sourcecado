"use client";
import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { AvocadoMark } from "./AvocadoMark";

export type NavItem = { label: string; href: string; icon?: ReactNode };

export function AppShell({
  nav,
  activeHref,
  user,
  inspector,
  children,
}: {
  nav: NavItem[];
  activeHref?: string;
  user?: { name: string; role: string };
  inspector?: ReactNode;
  children: ReactNode;
}) {
  // Derive the active route from the URL when the caller doesn't pass one
  // explicitly (the common case from the server-rendered root layout).
  const pathname = usePathname();
  const current = activeHref ?? pathname;
  return (
    <div
      className={`grid min-h-screen grid-cols-1 ${
        inspector ? "md:grid-cols-[232px_1fr_300px]" : "md:grid-cols-[232px_1fr]"
      }`}
    >
      {/* Mobile top bar: keeps branding present when the nav rail is hidden.
          A full mobile nav drawer is intentionally deferred (desktop-first tool). */}
      <header className="flex items-center gap-2 border-b border-border bg-raised px-4 py-3 md:hidden">
        <AvocadoMark size={20} />
        <span className="font-semibold tracking-tight">Sourcecado</span>
      </header>

      <aside className="hidden flex-col border-r border-border bg-raised p-3 md:flex">
        <Link
          href="/"
          className="mb-4 flex items-center gap-2.5 rounded-[7px] px-2 py-1 font-semibold tracking-tight focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-tint"
        >
          <AvocadoMark size={22} />
          Sourcecado
        </Link>
        <nav className="flex flex-col gap-0.5">
          {nav.map((item) => {
            const active = item.href === current;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`flex items-center gap-2.5 rounded-[7px] px-2.5 py-1.5 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-tint ${
                  active
                    ? "bg-accent-tint font-medium text-accent-deep"
                    : "text-text hover:bg-surface"
                }`}
              >
                {item.icon && <span className="flex h-4 w-4 items-center justify-center">{item.icon}</span>}
                {item.label}
              </Link>
            );
          })}
        </nav>
        {user && (
          <div className="mt-auto flex items-center gap-2.5 border-t border-border pt-3">
            <span className="grid h-[26px] w-[26px] place-items-center rounded-full bg-pit text-[11px] font-semibold text-white">
              {user.name.slice(0, 2).toUpperCase()}
            </span>
            <div>
              <div className="text-[12.5px] font-medium">{user.name}</div>
              <div className="text-[11px] text-muted">{user.role}</div>
            </div>
          </div>
        )}
      </aside>

      <main className="min-w-0 bg-canvas">{children}</main>

      {inspector && (
        <aside data-testid="inspector" className="hidden border-l border-border bg-surface p-4 md:block">
          {inspector}
        </aside>
      )}
    </div>
  );
}
