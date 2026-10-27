import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import type { SessionRow } from "../api";
import { RailIcon, type RailIconName } from "./RailIcon";
import type { AppRoute } from "./route";

// Mirrors the off-canvas breakpoint in styles/shell.css's
// `@media (max-width: 1179px)` block. Below it the rail is a fixed edge
// sheet hidden by a CSS transform when closed; a transform alone never
// removes an element from the tab order or the accessibility tree, so the
// closed sheet is additionally marked `inert` here. Above the breakpoint
// the rail is a permanently visible static sidebar and must stay focusable
// regardless of `open` (which nothing ever sets true there, since the
// hamburger trigger that would set it is itself CSS-hidden at that width).
const NARROW_RAIL_QUERY = "(max-width: 1179px)";

function useNarrowRail(): boolean {
  const [narrow, setNarrow] = useState(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return false;
    }
    try {
      return window.matchMedia(NARROW_RAIL_QUERY).matches;
    } catch {
      return false;
    }
  });

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    let mql: MediaQueryList;
    try {
      mql = window.matchMedia(NARROW_RAIL_QUERY);
    } catch {
      return;
    }
    const onChange = () => setNarrow(mql.matches);
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return narrow;
}

export type RailOperator = {
  // The connected Google account, the only sign-in identity Sourcecado has.
  email: string | null;
  persona: string;
};

type Props = {
  open: boolean;
  collapsed: boolean;
  operator: RailOperator | null;
  route: AppRoute;
  sessions: SessionRow[] | null;
  scheduledApprovalCount: number;
  memoryReviewCount: number;
  heldEffectCount: number;
  onToggleCollapsed: () => void;
  onNewChat: () => void;
  onOpenSession: (session: SessionRow) => void;
  onPin: (session: SessionRow) => void;
  onRename: (session: SessionRow, title: string) => Promise<void>;
  onSearch: () => void;
  onClose: () => void;
};

export function GlobalRail({ open, collapsed, operator, route, sessions, scheduledApprovalCount, memoryReviewCount, heldEffectCount, onToggleCollapsed, onNewChat, onOpenSession, onPin, onRename, onSearch, onClose }: Props) {
  const pinned = sessions?.filter((session) => session.pinned) || [];
  const recent = sessions?.filter((session) => !session.pinned) || [];
  const railRef = useRef<HTMLElement>(null);
  // Pinning moves a row to the other group. That remounts the row, so the
  // button the operator pressed disappears. Remember which row was pinned and
  // put focus on its pin button once it exists in the new group.
  const pinFocusRef = useRef<{ id: string; wasPinned: boolean } | null>(null);
  const narrow = useNarrowRail();
  const offScreen = narrow && !open;
  // The icon column is a wide-window choice. The narrow edge sheet always
  // shows full labels, whatever the stored choice says.
  const iconColumn = collapsed && !narrow;
  // Labels are hidden in the icon column, so each control names itself with a
  // tooltip. The expanded rail already shows the label.
  const tip = (label: string) => (iconColumn ? label : undefined);
  const operatorName = operator?.email || "Operator";
  const operatorTitle = operator?.persona ? `${operatorName} · ${operator.persona}` : operatorName;
  const operatorInitial = operator?.email ? operator.email.charAt(0).toUpperCase() : null;

  // `inert` isn't in @types/react's HTMLAttributes yet, so it's set as a
  // real DOM attribute rather than a JSX prop. This removes the closed
  // sheet's contents from both the tab order and the accessibility tree.
  useEffect(() => {
    const node = railRef.current;
    if (!node) return;
    if (offScreen) node.setAttribute("inert", "");
    else node.removeAttribute("inert");
  }, [offScreen]);

  useEffect(() => {
    const pending = pinFocusRef.current;
    if (!pending) return;
    const row = sessions?.find((session) => session.session_id === pending.id);
    if (!row || row.pinned === pending.wasPinned) return;
    pinFocusRef.current = null;
    // Focus fell to the page when the old button went away. If it is
    // anywhere else, the operator has moved on and keeps their place.
    if (document.activeElement && document.activeElement !== document.body) return;
    Array.from(railRef.current?.querySelectorAll<HTMLElement>("[data-thread-pin]") || [])
      .find((button) => button.dataset.threadPin === pending.id)
      ?.focus();
  }, [sessions]);

  function pinThread(session: SessionRow) {
    pinFocusRef.current = { id: session.session_id, wasPinned: session.pinned };
    onPin(session);
  }

  function trapFocus(event: KeyboardEvent<HTMLElement>) {
    if (!open || event.key !== "Tab") return;
    const focusable = Array.from(
      railRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled])'
      ) || []
    ).filter((element) => element.tabIndex !== -1);
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
  return (
    <aside
      ref={railRef}
      id="app-rail"
      className={`app-rail ${open ? "is-open" : ""} ${iconColumn ? "is-collapsed" : ""}`}
      data-open={open ? "true" : "false"}
      data-collapsed={iconColumn ? "true" : "false"}
      onKeyDown={trapFocus}
    >
      <nav aria-label="Sourcecado" className="app-rail-nav">
        <button id="app-rail-close" type="button" className="rail-close" aria-label="Close navigation" onClick={onClose}>
          <RailIcon name="close" size={20} />
        </button>
        <div className="rail-header">
          <div className="app-identity" aria-label="Sourcecado home">
            <img className="app-mark" src="/favicon.png" alt="" width={28} height={28} />
            <span className="rail-label">Sourcecado</span>
          </div>
          {!narrow && (
            <button
              type="button"
              className="rail-collapse-toggle"
              aria-label={iconColumn ? "Expand sidebar" : "Collapse sidebar"}
              aria-expanded={!iconColumn}
              data-tooltip={tip("Expand sidebar")}
              onClick={onToggleCollapsed}
            >
              <RailIcon name="collapse" />
            </button>
          )}
        </div>
        <button
          type="button"
          className="rail-search-button"
          aria-label="Search conversations and destinations"
          data-tooltip={tip("Search")}
          onClick={onSearch}
        >
          <RailIcon name="search" />
          <span className="rail-label">Search</span>
          <kbd className="rail-label">⌘K</kbd>
        </button>
        <button
          type="button"
          className="new-chat-button"
          aria-label="New chat"
          data-tooltip={tip("New chat")}
          onClick={onNewChat}
        >
          <RailIcon name="newChat" />
          <span className="rail-label">New chat</span>
        </button>
        <div className="destination-links">
          <RailLink href="#/board" icon="contacts" tooltip={tip("Contacts")} current={route.kind === "board" || route.kind === "person"}>Contacts</RailLink>
          <RailLink
            href="#/scheduled"
            icon="scheduled"
            tooltip={tip("Scheduled")}
            current={route.kind === "scheduled"}
            badge={scheduledApprovalCount}
          >
            Scheduled
          </RailLink>
          <RailLink href="#/connections" icon="connections" tooltip={tip("Connections")} current={route.kind === "connections"}>Connections</RailLink>
          <RailLink href="#/skills" icon="skills" tooltip={tip("Skills")} current={route.kind === "skills"}>Skills</RailLink>
          <RailLink
            href="#/memory"
            icon="memory"
            tooltip={tip("Memory")}
            current={route.kind === "memory"}
            badge={memoryReviewCount}
            badgeLabel={`Saved memory: ${memoryReviewCount} waiting for review`}
          >
            Memory
          </RailLink>
          <RailLink
            href="#/quarantine"
            icon="inbox"
            tooltip={tip("Inbox")}
            current={route.kind === "quarantine"}
            badge={heldEffectCount}
            badgeLabel={`Held actions: ${heldEffectCount} waiting for your answer`}
          >
            Inbox
          </RailLink>
        </div>
        <div className="thread-groups">
          {pinned.length > 0 && (
            <ThreadGroup label="Pinned threads" sessions={pinned} route={route} onOpenSession={onOpenSession} onPin={pinThread} onRename={onRename} />
          )}
          {sessions === null ? (
            <div className="thread-skeleton" aria-label="Loading conversations">
              <span />
              <span />
              <span />
            </div>
          ) : (
            <ThreadGroup label="Recent threads" sessions={recent} route={route} onOpenSession={onOpenSession} onPin={pinThread} onRename={onRename} />
          )}
        </div>
        <div className="rail-spacer" />
        <RailLink href="#/settings" icon="settings" tooltip={tip("Settings")} current={route.kind === "settings"}>Settings</RailLink>
        <div
          className="operator-identity"
          role="group"
          aria-label="Signed-in operator"
          title={iconColumn ? undefined : operatorTitle}
          data-tooltip={tip(operatorTitle)}
        >
          <span className="operator-avatar" aria-hidden="true">
            {operatorInitial ?? <RailIcon name="user" />}
          </span>
          <span className="operator-text rail-label">
            <strong className="operator-name">{operatorName}</strong>
            {operator?.persona && <span className="operator-persona">{operator.persona}</span>}
          </span>
        </div>
      </nav>
    </aside>
  );
}

function ThreadGroup({
  label,
  sessions,
  route,
  onOpenSession,
  onPin,
  onRename,
}: {
  label: string;
  sessions: SessionRow[];
  route: AppRoute;
  onOpenSession: (session: SessionRow) => void;
  onPin: (session: SessionRow) => void;
  onRename: (session: SessionRow, title: string) => Promise<void>;
}) {
  return (
    <section className="thread-group" role="group" aria-label={label}>
      {/* The group's accessible name comes from aria-label above (kept
          fuller, e.g. "Pinned threads") rather than this shortened visible
          text (e.g. "Pinned"), so this heading is not id-referenced. */}
      <p className="thread-group-label">{label.replace(" threads", "")}</p>
      {sessions.length === 0 ? (
        <p className="thread-empty">No conversations yet</p>
      ) : sessions.map((session) => (
        <ThreadRow
          key={session.session_id}
          session={session}
          active={route.kind === "chat" && route.sessionId === session.session_id}
          onOpenSession={onOpenSession}
          onPin={onPin}
          onRename={onRename}
        />
      ))}
    </section>
  );
}

function ThreadRow({
  session,
  active,
  onOpenSession,
  onPin,
  onRename,
}: {
  session: SessionRow;
  active: boolean;
  onOpenSession: (session: SessionRow) => void;
  onPin: (session: SessionRow) => void;
  onRename: (session: SessionRow, title: string) => Promise<void>;
}) {
  const title = session.title || "New conversation";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const renameButtonRef = useRef<HTMLButtonElement>(null);
  const refocusRenameRef = useRef(false);

  // The editor and the row are different elements. Closing the editor removes
  // the control that had focus, so hand focus back to the rename button.
  useEffect(() => {
    if (editing || !refocusRenameRef.current) return;
    refocusRenameRef.current = false;
    renameButtonRef.current?.focus();
  }, [editing]);

  function closeEditor() {
    refocusRenameRef.current = true;
    setEditing(false);
  }

  async function submitRename(event: FormEvent) {
    event.preventDefault();
    const cleaned = draft.trim();
    if (!cleaned) return;
    await onRename(session, cleaned);
    closeEditor();
  }

  if (editing) {
    return (
      <form
        className="thread-rename"
        onSubmit={(event) => void submitRename(event)}
        onKeyDown={(event) => {
          if (event.key !== "Escape") return;
          // Cancels the editor only. The open edge sheet must stay open.
          event.preventDefault();
          event.stopPropagation();
          closeEditor();
        }}
      >
        <input
          aria-label="Conversation title"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          autoFocus
        />
        <button type="submit" className="thread-action" aria-label={`Save ${title}`}>
          <RailIcon name="check" />
        </button>
        <button type="button" className="thread-action" aria-label={`Cancel renaming ${title}`} onClick={closeEditor}>
          <RailIcon name="close" />
        </button>
      </form>
    );
  }

  return (
    <div className="thread-row" data-active={active ? "true" : undefined}>
      <a
        className="thread-link"
        href={`#/chat/${encodeURIComponent(session.session_id)}`}
        aria-current={active ? "page" : undefined}
        onClick={() => onOpenSession(session)}
      >
        <span className="thread-title">{title}</span>
      </a>
      <span className="thread-actions">
        <button
          ref={renameButtonRef}
          type="button"
          className="thread-action"
          aria-label={`Rename ${title}`}
          onClick={() => {
            setDraft(title);
            setEditing(true);
          }}
        >
          <RailIcon name="rename" />
        </button>
        <button
          type="button"
          className="thread-action"
          data-thread-pin={session.session_id}
          aria-label={`${session.pinned ? "Unpin" : "Pin"} ${title}`}
          onClick={() => onPin(session)}
        >
          <RailIcon name="pin" filled={session.pinned} />
        </button>
      </span>
    </div>
  );
}

function RailLink({
  href,
  icon,
  tooltip,
  current,
  children,
  badge = 0,
  badgeLabel,
}: {
  href: string;
  icon: RailIconName;
  tooltip?: string;
  current: boolean;
  children: string;
  badge?: number;
  badgeLabel?: string;
}) {
  const descriptionId = useId();
  const countLabel =
    badgeLabel || `Inbox: ${badge} waiting approval${badge === 1 ? "" : "s"}`;
  return (
    <a
      className="rail-link"
      href={href}
      aria-label={children}
      aria-current={current ? "page" : undefined}
      aria-describedby={badge > 0 ? descriptionId : undefined}
      data-tooltip={tooltip && badge > 0 ? `${tooltip} (${badge})` : tooltip}
    >
      <RailIcon name={icon} />
      <span className="rail-label">{children}</span>
      {badge > 0 && (
        <>
          {/* The sighted count stops at 99+. The exact count stays in the
              accessible name and the description. The icon column shows a dot
              and the tooltip. */}
          <span className="rail-inbox-badge" aria-label={countLabel}>
            <span className="rail-badge-count">{badge > 99 ? "99+" : badge}</span>
          </span>
          <span id={descriptionId} className="rail-badge-description">
            {countLabel}
          </span>
        </>
      )}
    </a>
  );
}
