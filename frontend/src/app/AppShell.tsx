import { useEffect, useRef, useState } from "react";

import {
  createSession,
  getInbox,
  getMemoryBacklog,
  getQuarantinedEffects,
  pinSession,
  renameSession,
  setLastDestination,
  type SessionRow,
} from "../api";
import { BoardView } from "../Board";
import { PersonFileView } from "../PersonFile";
import { ChatPage } from "../routes/ChatPage";
import { moveDraft, readDraft } from "../chat/draftStorage";
import { ConnectionsPage } from "../routes/ConnectionsPage";
import { MemoryPage } from "../routes/MemoryPage";
import { QuarantinePage } from "../routes/QuarantinePage";
import { ScheduledPage } from "../routes/ScheduledPage";
import { SettingsPage } from "../routes/SettingsPage";
import { SkillsPage } from "../routes/SkillsPage";
import { PreviewChannelBadge } from "../routes/UpdateChannel";
import { UnavailableThreadPage } from "../routes/UnavailableThreadPage";
import { WelcomePage } from "../routes/WelcomePage";
import { CommandSearch } from "./CommandSearch";
import { GlobalRail } from "./GlobalRail";
import { parseHash } from "./route";
import { readShellCache, writeShellCache } from "./sessionCache";
import { getSessionsForBoot } from "./sessionBootstrap";

export function AppShell() {
  const [cachedListing] = useState(() => readShellCache());
  const cachedChatRestoreHash = cachedListing && isRestorableChatHash(window.location.hash)
    ? window.location.hash
    : null;
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  const [sessions, setSessions] = useState<SessionRow[] | null>(cachedListing?.sessions || null);
  const [openSessionId, setOpenSessionId] = useState<string | null>(cachedListing?.open_id || null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [bootError, setBootError] = useState<string | null>(null);
  const [bootAttempt, setBootAttempt] = useState(0);
  const [bootPending, setBootPending] = useState(true);
  const [stale, setStale] = useState(Boolean(cachedListing));
  const [railOpen, setRailOpen] = useState(false);
  const [scheduledApprovalCount, setScheduledApprovalCount] = useState(0);
  const [memoryReviewCount, setMemoryReviewCount] = useState(0);
  const [heldEffectCount, setHeldEffectCount] = useState(0);
  const railTriggerRef = useRef<HTMLButtonElement>(null);
  const railFocusTimerRef = useRef<number | null>(null);
  const railWasOpenedRef = useRef(false);
  const searchReturnFocusRef = useRef<HTMLElement | null>(null);
  const deferDestinationPersistenceRef = useRef(
    isRootHash(window.location.hash) || Boolean(cachedChatRestoreHash),
  );
  const bootRestoreHashRef = useRef<string | null>(cachedChatRestoreHash);
  function openSearch() {
    searchReturnFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setSearchOpen(true);
  }
  function closeSearch() {
    setSearchOpen(false);
    window.setTimeout(() => searchReturnFocusRef.current?.focus(), 0);
  }
  function closeRail() {
    if (railFocusTimerRef.current !== null) {
      window.clearTimeout(railFocusTimerRef.current);
      railFocusTimerRef.current = null;
    }
    setRailOpen(false);
    railTriggerRef.current?.focus();
  }
  useEffect(() => {
    const onHashChange = () => {
      setRoute(parseHash(window.location.hash));
      setRailOpen(false);
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  useEffect(() => {
    if (!railOpen) return;
    const onEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeRail();
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [railOpen]);
  useEffect(() => {
    if (railOpen) {
      railWasOpenedRef.current = true;
    } else if (railWasOpenedRef.current) {
      railTriggerRef.current?.focus();
    }
  }, [railOpen]);
  useEffect(() => {
    if (deferDestinationPersistenceRef.current) return;
    if (isRootHash(window.location.hash)) return;
    if (bootRestoreHashRef.current === window.location.hash) return;
    bootRestoreHashRef.current = null;
    if (route.kind === "chat" && route.sessionId?.startsWith("sched-")) return;
    setLastDestination(window.location.hash).catch(() => {});
  }, [route]);
  useEffect(() => {
    let active = true;
    const refreshInbox = () => {
      getInbox().then(
        ({ items }) => {
          if (!active) return;
          setScheduledApprovalCount(
            items.filter(
              (item) =>
                item.state === "pending" &&
                typeof item.session_id === "string" &&
                item.session_id.startsWith("sched-"),
            ).length,
          );
        },
        () => {},
      );
    };
    // Held external effects, on the same cadence. A held action is not an
    // approval waiting for a decision, so it gets its own count: one asks
    // "may I?" and the other asks "did it happen?".
    const refreshHeld = () => {
      getQuarantinedEffects().then(
        (effects) => {
          if (!active) return;
          setHeldEffectCount(effects.length);
        },
        () => {},
      );
    };
    refreshInbox();
    refreshHeld();
    window.addEventListener("focus", refreshInbox);
    window.addEventListener("focus", refreshHeld);
    window.addEventListener("sourcecado:inbox-changed", refreshInbox);
    return () => {
      active = false;
      window.removeEventListener("focus", refreshInbox);
      window.removeEventListener("focus", refreshHeld);
      window.removeEventListener("sourcecado:inbox-changed", refreshInbox);
    };
  }, []);
  useEffect(() => {
    let active = true;
    // Migrating to context-projection-v1 withholds every unclassified memory
    // row from the model. The count is how a director sees that Sourcecado is
    // waiting on them rather than that it forgot (issue #58).
    const refreshMemory = () => {
      getMemoryBacklog().then(
        ({ needs_review }) => {
          if (active) setMemoryReviewCount(needs_review);
        },
        () => {},
      );
    };
    refreshMemory();
    window.addEventListener("focus", refreshMemory);
    return () => {
      active = false;
      window.removeEventListener("focus", refreshMemory);
    };
  }, [route]);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        openSearch();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
  async function handleNewChat() {
    const created = await createSession();
    setSessions((current) => [
      {
        session_id: created.id,
        title: created.title,
        n_msgs: created.n_msgs,
        pinned: false,
        opened_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
      ...(current || []).filter((session) => session.session_id !== created.id),
    ]);
    setOpenSessionId(created.id);
    window.location.hash = `#/chat/${encodeURIComponent(created.id)}`;
  }
  async function handlePin(session: SessionRow) {
    const updated = await pinSession(session.session_id, !session.pinned);
    setSessions((current) =>
      current?.map((item) =>
        item.session_id === updated.id ? { ...item, pinned: updated.pinned } : item
      ) || []
    );
  }
  async function handleRename(session: SessionRow, title: string) {
    const updated = await renameSession(session.session_id, title);
    setSessions((current) =>
      current?.map((item) =>
        item.session_id === updated.id ? { ...item, title: updated.title } : item
      ) || []
    );
  }
  function handleOpenSession(session: SessionRow) {
    setOpenSessionId(session.session_id);
    setSessions((current) => current ? [
      session,
      ...current.filter((item) => item.session_id !== session.session_id),
    ] : current);
  }
  useEffect(() => {
    let active = true;
    setBootError(null);
    setBootPending(true);
    if (cachedListing && isRootHash(window.location.hash)) {
      const restored = cachedListing.last_destination ||
        (cachedListing.open_id ? `#/chat/${encodeURIComponent(cachedListing.open_id)}` : "");
      if (restored) {
        bootRestoreHashRef.current = restored;
        window.location.hash = restored;
        setRoute(parseHash(restored));
      } else if (cachedListing.sessions.length > 0) {
        bootRestoreHashRef.current = "#/chat";
        window.location.hash = "#/chat";
        setRoute({ kind: "chat" });
      }
    }
    getSessionsForBoot(() => active)
      .then(async (listing) => {
        if (!active) return;
        const restoreRoute = bootRestoreHashRef.current
          ? parseHash(bootRestoreHashRef.current)
          : null;
        const missingRestoreSessionId = restoreRoute?.kind === "chat" &&
          restoreRoute.sessionId &&
          !listing.sessions.some((session) => session.session_id === restoreRoute.sessionId)
            ? restoreRoute.sessionId
            : null;
        const hasRecoverableDraft = Boolean(
          missingRestoreSessionId && readDraft(missingRestoreSessionId),
        );
        if (
          missingRestoreSessionId &&
          (hasRecoverableDraft || listing.sessions.length === 0)
        ) {
          const created = await createSession();
          if (!active) return;
          if (hasRecoverableDraft) {
            moveDraft(missingRestoreSessionId, created.id);
          }
          const now = new Date().toISOString();
          const recoveredSession: SessionRow = {
            session_id: created.id,
            title: created.title,
            n_msgs: created.n_msgs,
            pinned: false,
            opened_at: now,
            updated_at: now,
          };
          const recoveredHash = `#/chat/${encodeURIComponent(created.id)}`;
          const recoveredListing = {
            sessions: [recoveredSession, ...listing.sessions],
            open_id: created.id,
            last_destination: recoveredHash,
          };
          setSessions(recoveredListing.sessions);
          setOpenSessionId(created.id);
          setStale(false);
          writeShellCache(recoveredListing);
          window.location.hash = recoveredHash;
          setRoute(parseHash(recoveredHash));
          bootRestoreHashRef.current = null;
          deferDestinationPersistenceRef.current = false;
          setBootPending(false);
          return;
        }
        setSessions(listing.sessions);
        setOpenSessionId(listing.open_id);
        setStale(false);
        writeShellCache(listing);
        const currentHash = window.location.hash;
        const cacheStillOwnsHash = bootRestoreHashRef.current === currentHash;
        const persistNavigationAfterBoot =
          deferDestinationPersistenceRef.current &&
          shouldPersistDestination(currentHash, cacheStillOwnsHash);
        if (isRootHash(currentHash) || cacheStillOwnsHash) {
          const restored = restoreHash(listing);
          if (restored) {
            window.location.hash = restored;
            setRoute(parseHash(restored));
          } else if (cacheStillOwnsHash) {
            window.location.hash = "#/";
            setRoute({ kind: "chat" });
          }
        }
        bootRestoreHashRef.current = null;
        deferDestinationPersistenceRef.current = false;
        if (persistNavigationAfterBoot) {
          setLastDestination(currentHash).catch(() => {});
        }
        setBootPending(false);
      })
      .catch((error: unknown) => {
        if (!active) return;
        const currentHash = window.location.hash;
        const cacheStillOwnsHash = bootRestoreHashRef.current === currentHash;
        const persistNavigationAfterBoot =
          deferDestinationPersistenceRef.current &&
          shouldPersistDestination(currentHash, cacheStillOwnsHash);
        if (!cacheStillOwnsHash) {
          bootRestoreHashRef.current = null;
        }
        deferDestinationPersistenceRef.current = false;
        if (persistNavigationAfterBoot) {
          setLastDestination(currentHash).catch(() => {});
        }
        setBootPending(false);
        setBootError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      active = false;
    };
  }, [bootAttempt, cachedListing]);
  let outlet;
  if (isRootHash(window.location.hash) && sessions === null) {
    outlet = (
      <main className="route-page boot-page" aria-busy="true">
        <h1>Sourcecado</h1>
        <div className="route-skeleton" aria-label="Loading workspace" />
      </main>
    );
  } else if (isRootHash(window.location.hash) && sessions?.length === 0) {
    outlet = <WelcomePage onStartChat={() => void handleNewChat()} />;
  } else if (isRootHash(window.location.hash)) {
    outlet = (
      <main className="route-page boot-page" aria-busy="true">
        <h1>Sourcecado</h1>
        <div className="route-skeleton" aria-label="Restoring workspace" />
      </main>
    );
  } else if (
    bootPending &&
    route.kind === "chat" &&
    bootRestoreHashRef.current === window.location.hash
  ) {
    outlet = (
      <main className="route-page boot-page" aria-busy="true">
        <h1>Sourcecado</h1>
        <div className="route-skeleton" aria-label="Restoring workspace" />
      </main>
    );
  } else if (route.kind === "board") {
    outlet = <BoardView />;
  } else if (route.kind === "person") {
    outlet = <PersonFileView personId={route.personId} />;
  } else if (route.kind === "skills") {
    outlet = <SkillsPage />;
  } else if (route.kind === "memory") {
    outlet = <MemoryPage />;
  } else if (route.kind === "scheduled") {
    outlet = <ScheduledPage jobId={route.jobId} />;
  } else if (route.kind === "quarantine") {
    outlet = <QuarantinePage />;
  } else if (route.kind === "connections") {
    outlet = <ConnectionsPage connectorId={route.connectorId} />;
  } else if (route.kind === "settings") {
    outlet = <SettingsPage />;
  } else if (
    route.sessionId &&
    !route.personId &&
    !route.sessionId.startsWith("sched-") &&
    sessions !== null &&
    !sessions.some((session) => session.session_id === route.sessionId)
  ) {
    outlet = <UnavailableThreadPage recentSessionId={openSessionId} />;
  } else {
    outlet = <ChatPage sessionId={route.sessionId} personId={route.personId} />;
  }
  return (
    <div className="app-shell">
      <PreviewChannelBadge />
      <button
        ref={railTriggerRef}
        type="button"
        className="rail-trigger"
        aria-label="Open navigation"
        aria-controls="app-rail"
        aria-expanded={railOpen}
        onClick={() => {
          setRailOpen(true);
          railFocusTimerRef.current = window.setTimeout(() => {
            document.getElementById("app-rail-close")?.focus();
            railFocusTimerRef.current = null;
          }, 0);
        }}
      >
        <span aria-hidden="true">☰</span>
      </button>
      <GlobalRail
        open={railOpen}
        route={route}
        sessions={sessions}
        scheduledApprovalCount={scheduledApprovalCount}
        memoryReviewCount={memoryReviewCount}
        heldEffectCount={heldEffectCount}
        onNewChat={() => void handleNewChat()}
        onOpenSession={handleOpenSession}
        onPin={(session) => void handlePin(session)}
        onRename={handleRename}
        onSearch={openSearch}
        onClose={closeRail}
      />
      {railOpen && (
        <button
          type="button"
          className="rail-scrim"
          aria-label="Close navigation overlay"
          onClick={closeRail}
        />
      )}
      <div className="shell-content">{outlet}</div>
      {bootError && (
        <div className="shell-alert" role="alert">
          <span>Couldn’t refresh conversations: {bootError}</span>
          <button type="button" onClick={() => setBootAttempt((attempt) => attempt + 1)}>Retry</button>
        </div>
      )}
      {stale && !bootPending && (
        <div className="shell-stale" role="status">
          <span>Cached conversations may be stale.</span>
          <button type="button" onClick={() => setBootAttempt((attempt) => attempt + 1)}>Reconnect</button>
        </div>
      )}
      {searchOpen && (
        <CommandSearch
          sessions={sessions || []}
          onClose={closeSearch}
          onNavigate={(hash) => {
            window.location.hash = hash;
            setSearchOpen(false);
          }}
        />
      )}
    </div>
  );
}

function isRootHash(hash: string) {
  return hash === "" || hash === "#" || hash === "#/";
}

function shouldPersistDestination(hash: string, bootRestoreOwnsHash: boolean): boolean {
  if (bootRestoreOwnsHash || isRootHash(hash)) return false;
  const destination = parseHash(hash);
  return !(destination.kind === "chat" && destination.sessionId?.startsWith("sched-"));
}

function isRestorableChatHash(hash: string): boolean {
  const route = parseHash(hash);
  return route.kind === "chat" &&
    Boolean(route.sessionId) &&
    !route.sessionId?.startsWith("sched-");
}

function restoreHash(listing: {
  sessions: SessionRow[];
  open_id: string | null;
  last_destination?: string | null;
}): string {
  const sessionIds = new Set(listing.sessions.map((session) => session.session_id));
  const lastDestination = listing.last_destination || "";
  if (lastDestination) {
    const lastRoute = parseHash(lastDestination);
    if (lastRoute.kind !== "chat") return lastDestination;
    if (lastRoute.sessionId && sessionIds.has(lastRoute.sessionId)) {
      return lastDestination;
    }
  }
  if (listing.open_id && sessionIds.has(listing.open_id)) {
    return `#/chat/${encodeURIComponent(listing.open_id)}`;
  }
  const firstSession = listing.sessions[0];
  return firstSession
    ? `#/chat/${encodeURIComponent(firstSession.session_id)}`
    : "";
}
