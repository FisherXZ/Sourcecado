import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type InspectorTarget = {
  readonly kind: "tool" | "source" | "artifact" | "legacy";
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
};

type InspectorContextValue = {
  readonly threadId: string;
  readonly selected: InspectorTarget | null;
  readonly select: (target: InspectorTarget, trigger: HTMLElement) => void;
  readonly close: () => void;
};

const InspectorContext = createContext<InspectorContextValue>({
  threadId: "",
  selected: null,
  select: () => {},
  close: () => {},
});

export function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? parsed.toString()
      : null;
  } catch {
    return null;
  }
}

/** The way out of the inspector to the original page: an icon, named for screen readers. */
function ExternalLink({ href }: { readonly href: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="sourcecado-inspector-external"
      aria-label="Open externally"
      title="Open externally"
    >
      <svg
        viewBox="0 0 16 16"
        width="16"
        height="16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        focusable="false"
      >
        <path d="M9 2.5h4.5V7M13.5 2.5l-6 6M12 9.5v3a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3" />
      </svg>
    </a>
  );
}

export function InspectorProvider({
  threadId,
  children,
}: {
  readonly threadId: string;
  readonly children: ReactNode;
}) {
  const [selected, setSelected] = useState<InspectorTarget | null>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  function close() {
    setSelected(null);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  }

  useEffect(() => {
    setSelected(null);
    triggerRef.current = null;
  }, [threadId]);

  useEffect(() => {
    if (!selected) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      close();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selected]);

  return (
    <InspectorContext.Provider
      value={{
        threadId,
        selected,
        select(target, trigger) {
          triggerRef.current = trigger;
          setSelected(target);
        },
        close,
      }}
    >
      {children}
    </InspectorContext.Provider>
  );
}

export function useInspector(): InspectorContextValue {
  return useContext(InspectorContext);
}

export function Inspector() {
  const { selected, close } = useInspector();
  const headingRef = useRef<HTMLHeadingElement>(null);

  // DU-10: the panel must receive focus when it opens (desktop pane,
  // tablet overlay, and narrow full-screen alike), not just return focus
  // on close. Re-fires on every new `selected` too, since selecting a
  // different row while the panel is already open swaps its content much
  // like a navigation.
  useEffect(() => {
    if (selected) headingRef.current?.focus();
  }, [selected]);

  if (!selected) return null;
  const externalUrl = safeExternalUrl(selected.externalUrl);
  return (
    <aside className="sourcecado-inspector" role="complementary" aria-label="Inspector">
      <header>
        <div>
          <p className="eyebrow">{selected.kind} detail</p>
          <h2 ref={headingRef} tabIndex={-1}>{selected.title || "Provenance detail"}</h2>
        </div>
        <button type="button" aria-label="Close inspector" onClick={close}>
          Close
        </button>
      </header>
      {selected.status === "loading" ? (
        <div className="sourcecado-inspector-skeleton" aria-label="Loading detail" />
      ) : selected.status === "error" ? (
        <div className="sourcecado-inspector-error" role="alert">
          <p>{selected.errorSummary ?? "This detail couldn’t be loaded."}</p>
          {selected.retry ? (
            <button type="button" onClick={selected.retry}>
              Retry detail
            </button>
          ) : null}
          {externalUrl ? (
            <ExternalLink href={externalUrl} />
          ) : selected.externalUrl ? (
            <p>External URL unavailable</p>
          ) : null}
        </div>
      ) : (
        <div className="sourcecado-inspector-content">
          {selected.stale ? <span className="sourcecado-inspector-badge">Cached stale</span> : null}
          {selected.truncated ? <span className="sourcecado-inspector-badge">Truncated</span> : null}
          {selected.provider ? <p>{selected.provider}</p> : null}
          {selected.preview ? <p>{selected.preview}</p> : null}
          {selected.timing ? (
            <p>
              Timing: {selected.timing.startedAt}
              {selected.timing.completedAt ? `–${selected.timing.completedAt}` : ""}
            </p>
          ) : null}
          {selected.args !== undefined ? (
            <details>
              <summary>Arguments</summary>
              <pre>{JSON.stringify(selected.args, null, 2)}</pre>
            </details>
          ) : null}
          {selected.result !== undefined ? (
            <details>
              <summary>Result</summary>
              <pre>{JSON.stringify(selected.result, null, 2)}</pre>
            </details>
          ) : null}
          {externalUrl ? (
            <ExternalLink href={externalUrl} />
          ) : selected.externalUrl ? (
            <p>External URL unavailable</p>
          ) : null}
        </div>
      )}
    </aside>
  );
}
