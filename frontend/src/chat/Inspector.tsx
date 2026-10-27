import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from "react";

import { InspectorIcon, type InspectorIconName } from "./InspectorIcons";
import {
  ContextPane,
  OutputPane,
  ProgressPane,
  hasContext,
  hasOutput,
} from "./InspectorPanes";
import { STEP_STATE_LABEL } from "./inspectorFormat";
import { ActionIcon, StatusIcon, ToolIcon } from "./ProcessIcons";
import type { StepState } from "./processState";
import type { ToolIconKey } from "./toolRegistry";

/*
 * The inspector is the one side panel for every preview in the thread: the
 * detail of a tool step, a retained source (#252) and an attached file
 * (#253). An item is an `InspectorTarget`. Its `kind` picks the header icon,
 * the kind label and the tabs, from the one table below. A kind with no tabs
 * shows its output and context in one scrolling view, like a preview. A new
 * kind adds one row to that table and one opener. It does not change the
 * panel.
 */
export type InspectorKind = "tool" | "source" | "artifact" | "legacy" | "file";

export type InspectorTabId = "progress" | "output" | "context";

/** One step of the turn, for the Progress tab. */
export type InspectorStep = {
  readonly id: string;
  readonly label: string;
  readonly icon: ToolIconKey;
  readonly state: StepState;
  readonly startedAt?: number;
  readonly completedAt?: number;
};

export type InspectorTarget = {
  readonly kind: InspectorKind;
  readonly id: string;
  readonly title: string;
  readonly status?: "loading" | "success" | "error";
  readonly provider?: string;
  readonly externalUrl?: string | null;
  readonly args?: unknown;
  readonly result?: unknown;
  readonly timing?: { readonly startedAt: number; readonly completedAt?: number };
  readonly preview?: string | null;
  readonly stale?: boolean;
  readonly truncated?: boolean;
  readonly errorSummary?: string;
  readonly retry?: () => void;
  /** Tool steps. The internal tool name, shown in Context. */
  readonly toolName?: string;
  /** Tool steps. The header icon. */
  readonly icon?: ToolIconKey;
  /** Tool steps. The status chip and the Progress list read this. */
  readonly state?: StepState;
  /** Tool steps. Every step of the turn, for the Progress tab. */
  readonly steps?: readonly InspectorStep[];
  /** Facts for the Output summary, such as the failure class. */
  readonly facts?: readonly { readonly label: string; readonly value: string }[];
  /** The result rendered as the thread's own card. Output shows it first. */
  readonly card?: ReactNode;
  /** One line for the Output tab when there is no result to show. */
  readonly note?: string;
  /** Changes whenever the live data behind a tool step changes. */
  readonly revision?: string;
};

type InspectorKindMeta = {
  readonly label: string;
  readonly tabs: readonly InspectorTabId[];
  readonly icon: (target: InspectorTarget) => ReactNode;
};

export const INSPECTOR_KINDS: Readonly<Record<InspectorKind, InspectorKindMeta>> = {
  tool: {
    label: "Tool result",
    tabs: ["progress", "output", "context"],
    icon: (target) => <ToolIcon name={target.icon ?? "tool"} />,
  },
  source: {
    label: "Source",
    tabs: [],
    icon: () => <InspectorIcon name="source" />,
  },
  file: {
    label: "Attached file",
    tabs: [],
    icon: () => <ToolIcon name="file" />,
  },
  artifact: {
    label: "Artifact",
    tabs: [],
    icon: () => <InspectorIcon name="artifact" />,
  },
  legacy: {
    label: "Detail",
    tabs: [],
    icon: () => <InspectorIcon name="detail" />,
  },
};

const TAB_META: Readonly<
  Record<InspectorTabId, { readonly label: string; readonly icon: InspectorIconName }>
> = {
  progress: { label: "Progress", icon: "progress" },
  output: { label: "Output", icon: "output" },
  context: { label: "Context", icon: "context" },
};

type InspectorContextValue = {
  readonly threadId: string;
  readonly selected: InspectorTarget | null;
  /** Bumps on every open, so the panel can take focus on a repeat click. */
  readonly openCount: number;
  readonly select: (target: InspectorTarget, trigger: HTMLElement) => void;
  /** Replaces the open item with newer data for the same item. */
  readonly sync: (target: InspectorTarget) => void;
  readonly close: () => void;
};

const InspectorContext = createContext<InspectorContextValue>({
  threadId: "",
  selected: null,
  openCount: 0,
  select: () => {},
  sync: () => {},
  close: () => {},
});

/** Where focus goes on close if the control that opened the panel is gone. */
type ReturnAnchor = {
  readonly callId: string | null;
  readonly activity: Element | null;
};

function anchorOf(trigger: HTMLElement): ReturnAnchor {
  return {
    callId: trigger.closest("[data-tool-call-id]")?.getAttribute("data-tool-call-id") ?? null,
    activity: trigger.closest(".sourcecado-activity"),
  };
}

/**
 * The control to focus on close: the opener. If the operator closed its step
 * while the panel was open, the step row. If the whole process layer closed,
 * its receipt line.
 */
function returnTarget(trigger: HTMLElement | null, anchor: ReturnAnchor): HTMLElement | null {
  if (trigger?.isConnected) return trigger;
  const row = [...document.querySelectorAll("[data-tool-call-id]")].find(
    (element) => anchor.callId !== null && element.getAttribute("data-tool-call-id") === anchor.callId,
  );
  if (row) return row.querySelector<HTMLElement>("button");
  return anchor.activity?.isConnected
    ? anchor.activity.querySelector<HTMLElement>(".sourcecado-activity-receipt, button")
    : null;
}

export function InspectorProvider({
  threadId,
  children,
}: {
  readonly threadId: string;
  readonly children: ReactNode;
}) {
  const [selected, setSelected] = useState<InspectorTarget | null>(null);
  const [openCount, setOpenCount] = useState(0);
  const triggerRef = useRef<HTMLElement | null>(null);
  const anchorRef = useRef<ReturnAnchor>({ callId: null, activity: null });
  const open = selected !== null;

  const close = useCallback(() => {
    setSelected(null);
    window.setTimeout(
      () => returnTarget(triggerRef.current, anchorRef.current)?.focus(),
      0,
    );
  }, []);

  const select = useCallback((target: InspectorTarget, trigger: HTMLElement) => {
    // A click inside the panel (a button on the card) swaps the item. Focus
    // still returns to the control that first opened the panel.
    if (!trigger.closest(".sourcecado-inspector")) {
      triggerRef.current = trigger;
      anchorRef.current = anchorOf(trigger);
    }
    setSelected(target);
    setOpenCount((count) => count + 1);
  }, []);

  const sync = useCallback((target: InspectorTarget) => {
    setSelected((current) => (current && current.id === target.id ? target : current));
  }, []);

  useEffect(() => {
    setSelected(null);
    triggerRef.current = null;
    anchorRef.current = { callId: null, activity: null };
  }, [threadId]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      // A control that already used Escape (a menu, a dialog) keeps it.
      if (event.key !== "Escape" || event.defaultPrevented) return;
      event.preventDefault();
      close();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, close]);

  const value = useMemo(
    () => ({ threadId, selected, openCount, select, sync, close }),
    [threadId, selected, openCount, select, sync, close],
  );
  return <InspectorContext.Provider value={value}>{children}</InspectorContext.Provider>;
}

export function useInspector(): InspectorContextValue {
  return useContext(InspectorContext);
}

const WIDTH_KEY = "sourcecado.inspector.width.v1";
export const INSPECTOR_DEFAULT_WIDTH = 400;
export const INSPECTOR_MIN_WIDTH = 320;
export const INSPECTOR_MAX_WIDTH = 720;
/** The thread keeps at least this much room beside a docked panel. */
const THREAD_MIN_WIDTH = 440;
const KEY_STEP = 16;
/** Matches the CSS rule that makes the panel fill the window. */
const FULL_SCREEN_QUERY = "(max-width: 767px)";
const TAB_STOPS =
  'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

/** Keeps a panel width between the minimum and what the workspace can spare. */
export function clampInspectorWidth(width: number, available: number): number {
  const room = available > 0 ? available - THREAD_MIN_WIDTH : INSPECTOR_MAX_WIDTH;
  const max = Math.max(INSPECTOR_MIN_WIDTH, Math.min(INSPECTOR_MAX_WIDTH, room));
  return Math.round(Math.min(Math.max(width, INSPECTOR_MIN_WIDTH), max));
}

function readWidth(): number {
  try {
    const stored = Number(window.localStorage.getItem(WIDTH_KEY));
    return Number.isFinite(stored) && stored >= INSPECTOR_MIN_WIDTH
      ? Math.min(stored, INSPECTOR_MAX_WIDTH)
      : INSPECTOR_DEFAULT_WIDTH;
  } catch {
    return INSPECTOR_DEFAULT_WIDTH;
  }
}

function writeWidth(width: number): void {
  try {
    window.localStorage.setItem(WIDTH_KEY, String(width));
  } catch {
    // The width is a convenience. The panel works without it.
  }
}

function chipOf(
  target: InspectorTarget,
): { readonly state: StepState; readonly label: string } | null {
  if (target.state) return { state: target.state, label: STEP_STATE_LABEL[target.state] };
  if (target.status === "loading") return { state: "running", label: "Loading" };
  if (target.status === "error") return { state: "failed", label: "Failed" };
  return null;
}

function defaultTab(target: InspectorTarget, tabs: readonly InspectorTabId[]): InspectorTabId {
  // A step that has not finished has no output yet. Progress is the useful view.
  const unfinished = target.state === "running" || target.state === "waiting";
  if (unfinished && tabs.includes("progress")) return "progress";
  return tabs.includes("output") ? "output" : tabs[0]!;
}

export function Inspector() {
  const { selected, openCount, close } = useInspector();
  const panelRef = useRef<HTMLElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const dragRef = useRef<{ startX: number; startWidth: number; available: number; width: number } | null>(null);
  // The width the operator chose. The width shown is this, held to the room
  // the workspace has now: a width stored on a wide window must not squeeze
  // the thread on a narrower one.
  const [width, setWidth] = useState(readWidth);
  const [room, setRoom] = useState(0);
  const [tabChoice, setTabChoice] = useState<{ id: string; tab: InspectorTabId } | null>(null);
  // The tab an item opens on is chosen once. A step that finishes while the
  // panel is open does not move the operator to another tab.
  const openedOnRef = useRef<{ id: string; tab: InspectorTabId } | null>(null);
  const baseId = useId();

  // DU-10: the panel takes focus when it opens (docked, overlay and full
  // screen alike). `openCount` also moves focus when the operator opens the
  // item that is already open, and live updates to the item do not.
  useEffect(() => {
    if (openCount > 0) headingRef.current?.focus({ preventScroll: true });
  }, [openCount]);

  const open = selected !== null;
  const fullScreenRef = useRef(false);

  // Measure the room beside the thread when the panel opens and whenever the
  // window or workspace changes size. An overlay panel does not sit beside the
  // thread, so it has no room limit (0 means no limit).
  useLayoutEffect(() => {
    const panel = panelRef.current;
    const workspace = panel?.parentElement;
    if (!open || !panel || !workspace) return;
    const measure = () =>
      setRoom(window.getComputedStyle(panel).position === "fixed" ? 0 : workspace.clientWidth);
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(workspace);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [open]);

  // A full-screen panel hides the thread, so the thread must not take focus.
  // Escape still closes the panel. The thread is live again before focus goes
  // back to the control that opened the panel.
  useEffect(() => {
    const panel = panelRef.current;
    const workspace = panel?.parentElement;
    if (!open || !panel || !workspace || typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(FULL_SCREEN_QUERY);
    let held: Element[] = [];
    const release = () => {
      held.forEach((element) => element.removeAttribute("inert"));
      held = [];
    };
    const apply = () => {
      release();
      fullScreenRef.current = query.matches;
      if (!query.matches) return;
      held = [...workspace.children].filter((child) => child !== panel);
      held.forEach((element) => element.setAttribute("inert", ""));
    };
    apply();
    query.addEventListener("change", apply);
    return () => {
      query.removeEventListener("change", apply);
      release();
      fullScreenRef.current = false;
    };
  }, [open]);

  if (!selected) return null;
  const shownWidth = clampInspectorWidth(width, room);
  const meta = INSPECTOR_KINDS[selected.kind];
  // Progress lists the steps of a turn. An item opened from a card has none.
  const tabs = meta.tabs.filter(
    (id) => id !== "progress" || (selected.steps?.length ?? 0) > 0,
  );
  const tabbed = tabs.length > 0;
  if (tabbed && openedOnRef.current?.id !== selected.id) {
    openedOnRef.current = { id: selected.id, tab: defaultTab(selected, tabs) };
  }
  const activeTab: InspectorTabId | null = !tabbed
    ? null
    : tabChoice?.id === selected.id && tabs.includes(tabChoice.tab)
      ? tabChoice.tab
      : openedOnRef.current!.tab;
  const chip = chipOf(selected);
  const errorMessage =
    selected.status === "error"
      ? (selected.errorSummary ?? "This detail couldn’t be loaded.")
      : null;

  const commitWidth = (next: number) => {
    const clamped = clampInspectorWidth(next, room);
    setWidth(clamped);
    writeWidth(clamped);
  };

  // In the full-screen panel, Tab stays inside it. The thread is inert, but
  // the app's own controls (the navigation button) sit outside the thread.
  const onPanelKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const panel = panelRef.current;
    if (event.key !== "Tab" || !fullScreenRef.current || !panel) return;
    const stops = [...panel.querySelectorAll<HTMLElement>(TAB_STOPS)].filter(
      (element) => window.getComputedStyle(element).display !== "none",
    );
    const first = stops[0];
    const last = stops[stops.length - 1];
    if (!first || !last) return;
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !stops.includes(active as HTMLElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const onTabKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = activeTab ? tabs.indexOf(activeTab) : 0;
    const next =
      event.key === "ArrowRight"
        ? tabs[(index + 1) % tabs.length]
        : event.key === "ArrowLeft"
          ? tabs[(index - 1 + tabs.length) % tabs.length]
          : event.key === "Home"
            ? tabs[0]
            : event.key === "End"
              ? tabs[tabs.length - 1]
              : undefined;
    if (!next) return;
    event.preventDefault();
    setTabChoice({ id: selected.id, tab: next });
    document.getElementById(`${baseId}-tab-${next}`)?.focus();
  };

  const onResizeDown = (event: PointerEvent<HTMLDivElement>) => {
    const panel = panelRef.current;
    if (!panel || event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const startWidth = panel.getBoundingClientRect().width;
    dragRef.current = {
      startX: event.clientX,
      startWidth,
      available: room,
      width: startWidth,
    };
  };

  const onResizeMove = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    const panel = panelRef.current;
    if (!drag || !panel) return;
    // The handle is on the left edge, so dragging left makes the panel wider.
    drag.width = clampInspectorWidth(drag.startWidth + drag.startX - event.clientX, drag.available);
    panel.style.setProperty("--inspector-width", `${drag.width}px`);
  };

  const onResizeEnd = () => {
    const drag = dragRef.current;
    dragRef.current = null;
    if (drag) commitWidth(drag.width);
  };

  const onResizeKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? KEY_STEP * 3 : KEY_STEP;
    const next =
      event.key === "ArrowLeft"
        ? shownWidth + step
        : event.key === "ArrowRight"
          ? shownWidth - step
          : event.key === "Home"
            ? INSPECTOR_MIN_WIDTH
            : event.key === "End"
              ? INSPECTOR_MAX_WIDTH
              : null;
    if (next === null) return;
    event.preventDefault();
    commitWidth(next);
  };

  return (
    <aside
      ref={panelRef}
      className="sourcecado-inspector"
      data-kind={selected.kind}
      role="complementary"
      aria-label="Inspector"
      onKeyDown={onPanelKeyDown}
      style={{ "--inspector-width": `${shownWidth}px` } as CSSProperties}
    >
      <div
        className="sourcecado-inspector-resize"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize inspector"
        aria-valuenow={shownWidth}
        aria-valuemin={INSPECTOR_MIN_WIDTH}
        aria-valuemax={INSPECTOR_MAX_WIDTH}
        tabIndex={0}
        onPointerDown={onResizeDown}
        onPointerMove={onResizeMove}
        onPointerUp={onResizeEnd}
        onPointerCancel={onResizeEnd}
        onKeyDown={onResizeKeyDown}
        onDoubleClick={() => commitWidth(INSPECTOR_DEFAULT_WIDTH)}
      />
      <header className="sourcecado-inspector-head">
        <span className="sourcecado-inspector-kind-icon">{meta.icon(selected)}</span>
        <div className="sourcecado-inspector-titles">
          <h2 ref={headingRef} tabIndex={-1} title={selected.title || undefined}>
            {selected.title || "Provenance detail"}
          </h2>
          <p className="sourcecado-inspector-kind">{meta.label}</p>
        </div>
        {chip ? (
          <span className={`sourcecado-inspector-chip sourcecado-inspector-chip-${chip.state}`}>
            <StatusIcon state={chip.state} />
            {chip.label}
          </span>
        ) : null}
        <button
          type="button"
          className="sourcecado-inspector-close"
          aria-label="Close inspector"
          title="Close"
          onClick={close}
        >
          <InspectorIcon name="close" />
        </button>
      </header>
      {errorMessage ? (
        <div className="sourcecado-inspector-error" role="alert">
          <p>{errorMessage}</p>
          {selected.retry ? (
            <button type="button" onClick={selected.retry}>
              <ActionIcon name="retry" />
              Retry detail
            </button>
          ) : null}
        </div>
      ) : null}
      {tabbed ? (
        <>
          <div
            className="sourcecado-inspector-tabs"
            role="tablist"
            aria-label="Inspector sections"
            onKeyDown={onTabKeyDown}
          >
            {tabs.map((id) => (
              <button
                key={id}
                type="button"
                role="tab"
                id={`${baseId}-tab-${id}`}
                aria-selected={id === activeTab}
                aria-controls={`${baseId}-panel`}
                tabIndex={id === activeTab ? 0 : -1}
                onClick={() => setTabChoice({ id: selected.id, tab: id })}
              >
                <InspectorIcon name={TAB_META[id].icon} />
                {TAB_META[id].label}
              </button>
            ))}
          </div>
          <div
            className="sourcecado-inspector-panel"
            role="tabpanel"
            id={`${baseId}-panel`}
            aria-labelledby={`${baseId}-tab-${activeTab}`}
            tabIndex={0}
          >
            {activeTab === "progress" ? <ProgressPane target={selected} /> : null}
            {activeTab === "output" ? <OutputPane key={selected.id} target={selected} /> : null}
            {activeTab === "context" ? <ContextPane target={selected} /> : null}
          </div>
        </>
      ) : (
        <div className="sourcecado-inspector-panel" tabIndex={0}>
          {hasOutput(selected) ? <OutputPane key={selected.id} target={selected} /> : null}
          {hasContext(selected) ? <ContextPane target={selected} /> : null}
          {!hasOutput(selected) && !hasContext(selected) ? (
            <p className="sourcecado-inspector-empty">Nothing to show.</p>
          ) : null}
        </div>
      )}
    </aside>
  );
}
