import {
  Component,
  useId,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";

import { useInspector, type InspectorTarget } from "../chat/Inspector";
import { ToolIcon } from "../chat/ProcessIcons";
import type { DomainRendererProps, ToolIconKey } from "../chat/toolRegistry";
import { sanitizeApolloNameMasks } from "../personName";

/**
 * One chrome for every structured result (#234): a header with the provider
 * icon, the title and a count; rows 36px tall; a footer with icon actions.
 *
 * A card is always in exactly one state, and every state has its own icon and
 * word, so a quiet card never reads as success:
 *   loading      the call is still running
 *   empty        the call worked and found nothing
 *   partial      some of the data is missing
 *   stale        the data is a cached copy that may be out of date
 *   restricted   Sourcecado is not allowed to read it
 *   failed       the call did not complete
 *   unsupported  the data has a shape Sourcecado cannot read
 */
export type CardState =
  | "ready"
  | "loading"
  | "empty"
  | "partial"
  | "stale"
  | "restricted"
  | "failed"
  | "unsupported";

const STATE_WORDS: Readonly<Record<Exclude<CardState, "ready">, string>> = {
  loading: "Loading",
  empty: "Empty",
  partial: "Partial",
  stale: "Stale",
  restricted: "Restricted",
  failed: "Failed",
  unsupported: "Unsupported",
};

const GLYPHS = {
  inspect: (
    <>
      <path d="M1.8 8S4 3.8 8 3.8 14.2 8 14.2 8 12 12.2 8 12.2 1.8 8 1.8 8z" />
      <circle cx="8" cy="8" r="1.8" />
    </>
  ),
  chevron: <path d="m4 6 4 4 4-4" />,
  keep: (
    <>
      <circle cx="6.5" cy="5.5" r="2.5" />
      <path d="M2 13.5c.5-2.4 2.3-3.7 4.5-3.7M12 6.5v4M10 8.5h4" />
    </>
  ),
  review: (
    <path d="m2.5 4.5 1.2 1.2 2-2.2M2.5 10.5l1.2 1.2 2-2.2M8 5h5.5M8 11h5.5" />
  ),
  info: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 7.4v3.4M8 5.2v.1" />
    </>
  ),
  user: (
    <>
      <circle cx="8" cy="5.5" r="2.5" />
      <path d="M3 13.5c.6-2.6 2.6-4 5-4s4.4 1.4 5 4" />
    </>
  ),
  mail: (
    <>
      <rect x="2" y="3.5" width="12" height="9" rx="1.5" />
      <path d="m2.5 4.8 5.5 4.4 5.5-4.4" />
    </>
  ),
  phone: (
    <path d="M4 2.5h2l1 3-1.5 1a7 7 0 0 0 3 3l1-1.5 3 1v2a1.5 1.5 0 0 1-1.5 1.5A10 10 0 0 1 2.5 4 1.5 1.5 0 0 1 4 2.5z" />
  ),
  link: (
    <path d="M6.5 9.5a3 3 0 0 0 4.2 0l2-2a3 3 0 0 0-4.2-4.2l-.8.8M9.5 6.5a3 3 0 0 0-4.2 0l-2 2a3 3 0 0 0 4.2 4.2l.8-.8" />
  ),
  clock: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 5v3.2l2 1.3" />
    </>
  ),
  doc: (
    <>
      <path d="M4 2.5h5l3 3v8H4z" />
      <path d="M9 2.5v3h3M6 8.5h4M6 11h4" />
    </>
  ),
  card: (
    <>
      <rect x="2.5" y="3.5" width="11" height="9" rx="1.5" />
      <path d="M2.5 6.5h11M5 9.5h3" />
    </>
  ),
  adjust: <path d="M3 5h10M3 11h10M6 3.5v3M10 9.5v3" />,
  chat: <path d="M2.5 3.5h11v7H8l-3 2.5v-2.5H2.5z" />,
  retry: <path d="M13 8a5 5 0 1 1-1.6-3.7M13 2.5v3h-3" />,
  loading: (
    <>
      <circle cx="8" cy="8" r="5.5" opacity=".25" />
      <path d="M8 2.5A5.5 5.5 0 0 1 13.5 8" />
    </>
  ),
  empty: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M5.5 8h5" />
    </>
  ),
  partial: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 2.5v11a5.5 5.5 0 0 1 0-11z" fill="currentColor" />
    </>
  ),
  stale: (
    <>
      <path d="M13 8a5 5 0 1 1-1.6-3.7M13 2.5v3h-3" />
      <path d="M8 5.6V8l1.6 1" />
    </>
  ),
  restricted: (
    <>
      <rect x="3.5" y="7" width="9" height="6.5" rx="1.5" />
      <path d="M5.5 7V5.5a2.5 2.5 0 0 1 5 0V7" />
    </>
  ),
  failed: (
    <>
      <circle cx="8" cy="8" r="5.5" />
      <path d="m5.8 5.8 4.4 4.4M10.2 5.8l-4.4 4.4" />
    </>
  ),
  unsupported: (
    <>
      <path d="M4 2.5h5l3 3v8H4z" />
      <path d="M6.7 8a1.4 1.4 0 1 1 2.1 1.2c-.5.3-.8.6-.8 1.2M8 12v.1" />
    </>
  ),
} as const;

export type GlyphName = keyof typeof GLYPHS;

export function Glyph({ name }: { readonly name: GlyphName }) {
  return (
    <svg
      className={`sourcecado-card-glyph sourcecado-card-glyph-${name}`}
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
      {GLYPHS[name]}
    </svg>
  );
}

export function ResultCard({
  id,
  toolCallIds,
  provider,
  title,
  count,
  tag,
  meta,
  state = "ready",
  note,
  children,
  footer,
}: {
  readonly id?: string;
  /** Every tool call this card shows, so a step row can find it. */
  readonly toolCallIds?: readonly string[];
  readonly provider: ToolIconKey;
  readonly title: string;
  readonly count?: string | null;
  /** A fact the operator must not miss, such as "Not sent" on a draft. */
  readonly tag?: ReactNode;
  /** Short tags under the header, such as the search the card answers. */
  readonly meta?: readonly string[];
  readonly state?: CardState;
  /** One factual line for a state other than ready. */
  readonly note?: ReactNode;
  readonly children?: ReactNode;
  readonly footer?: ReactNode;
}) {
  const titleId = useId();
  return (
    <section
      id={id}
      className="sourcecado-card"
      data-state={state}
      data-card-tools={toolCallIds?.join(" ")}
      aria-labelledby={titleId}
      aria-busy={state === "loading" ? true : undefined}
    >
      <header className="sourcecado-card-header">
        <span className="sourcecado-card-provider">
          <ToolIcon name={provider} />
        </span>
        <h3 id={titleId} tabIndex={-1}>
          {title}
        </h3>
        {count ? <span className="sourcecado-card-count">{count}</span> : null}
        {tag ? <strong className="sourcecado-card-tag">{tag}</strong> : null}
        {state !== "ready" ? (
          <span className="sourcecado-card-state" data-state={state}>
            <Glyph name={state} />
            <span>{STATE_WORDS[state]}</span>
          </span>
        ) : null}
      </header>
      {meta && meta.length > 0 ? (
        <div className="sourcecado-card-meta">
          {meta.map((tag) => (
            <span key={tag} className="sourcecado-card-chip">
              {tag}
            </span>
          ))}
        </div>
      ) : null}
      {note ? (
        <p className="sourcecado-card-note" data-state={state}>
          {note}
        </p>
      ) : null}
      {children ? (
        <div
          className="sourcecado-card-body"
          role="group"
          aria-labelledby={titleId}
          tabIndex={0}
        >
          {children}
        </div>
      ) : null}
      {footer ? <footer className="sourcecado-card-footer">{footer}</footer> : null}
    </section>
  );
}

/** The rows of a card. The list grows to its widest row, so the card body
 * scrolls sideways at a narrow width and the page never does. */
export function CardRows({
  label,
  id,
  children,
}: {
  readonly label: string;
  readonly id?: string;
  readonly children: ReactNode;
}) {
  return (
    <ol id={id} className="sourcecado-card-rows" aria-label={label}>
      {children}
    </ol>
  );
}

export function CardRow({
  children,
  tall = false,
}: {
  readonly children: ReactNode;
  /** A row with a paragraph of text may grow past 36px. */
  readonly tall?: boolean;
}) {
  return (
    <li className={`sourcecado-card-row${tall ? " sourcecado-card-row-tall" : ""}`}>
      {children}
    </li>
  );
}

/** A labelled value row (Email, Subject, Target). */
export function CardField({
  icon,
  label,
  children,
}: {
  readonly icon: GlyphName;
  readonly label: string;
  readonly children: ReactNode;
}) {
  return (
    <li className="sourcecado-card-row sourcecado-card-field">
      <span className="sourcecado-card-key">
        <Glyph name={icon} />
        <span>{label}</span>
      </span>
      <span className="sourcecado-card-value">{children}</span>
    </li>
  );
}

/** A value the source did not give. It shows as a gap, never as data. */
export function Gap({ children }: { readonly children: ReactNode }) {
  return <span className="sourcecado-card-gap">{children}</span>;
}

export function CardBadge({
  children,
  tone = "warn",
}: {
  readonly children: ReactNode;
  readonly tone?: "warn" | "neutral" | "error";
}) {
  return (
    <span className="sourcecado-card-badge" data-tone={tone}>
      {children}
    </span>
  );
}

export function CardButton({
  icon,
  children,
  tone = "quiet",
  ...rest
}: {
  readonly icon: GlyphName;
  readonly children: ReactNode;
  readonly tone?: "quiet" | "primary";
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type" | "children">) {
  return (
    <button
      type="button"
      className="sourcecado-card-button"
      data-tone={tone}
      {...rest}
    >
      <Glyph name={icon} />
      <span>{children}</span>
    </button>
  );
}

/** The footer action every card has: open what the card shows in the inspector. */
export function InspectButton({ target }: { readonly target: InspectorTarget }) {
  const { select } = useInspector();
  return (
    <CardButton
      icon="inspect"
      aria-label={`Open in inspector: ${target.title}`}
      onClick={(event) => select(target, event.currentTarget)}
    >
      Open in inspector
    </CardButton>
  );
}

/** Text that is clipped until the operator opens it. The button names what it opens. */
export function ExpandableText({
  text,
  limit = 280,
  expandLabel,
  collapseLabel,
  className,
}: {
  readonly text: string;
  readonly limit?: number;
  readonly expandLabel: string;
  readonly collapseLabel: string;
  readonly className?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const textId = useId();
  const truncated = text.length > limit;
  const visible =
    truncated && !expanded ? `${text.slice(0, limit).trimEnd()}…` : text;
  return (
    <div className={`sourcecado-card-text${className ? ` ${className}` : ""}`}>
      <p id={textId}>{visible}</p>
      {truncated ? (
        <CardButton
          icon="chevron"
          aria-controls={textId}
          aria-expanded={expanded}
          data-open={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? collapseLabel : expandLabel}
        </CardButton>
      ) : null}
    </div>
  );
}

export function SkeletonRows({
  label,
  rows = 3,
}: {
  readonly label: string;
  readonly rows?: number;
}) {
  return (
    <ol
      className="sourcecado-card-rows sourcecado-card-skeleton"
      aria-label={label}
      aria-busy="true"
    >
      {Array.from({ length: rows }, (_, row) => (
        <li key={row} className="sourcecado-card-row">
          <span />
          <span />
        </li>
      ))}
    </ol>
  );
}

/** What the inspector shows for a result the card cannot read. Apollo name masks stay out. */
export function rawToolTarget(
  props: Pick<DomainRendererProps, "toolCallId" | "toolName" | "args" | "result">,
  title: string,
): InspectorTarget {
  const apollo = props.toolName.startsWith("apollo_");
  try {
    return {
      kind: "tool",
      id: props.toolCallId,
      title,
      status: "success",
      args: apollo ? sanitizeApolloNameMasks(props.args) : props.args,
      result: apollo ? sanitizeApolloNameMasks(props.result) : props.result,
    };
  } catch {
    // A record that cannot be read is the reason this card is a fallback.
    return {
      kind: "tool",
      id: props.toolCallId,
      title,
      status: "success",
      preview: "The raw record could not be read.",
    };
  }
}

/**
 * The fallback for a result whose shape the card does not know. It says so in
 * words and points to the inspector. It never prints the data.
 */
export function UnsupportedCard({
  cardId,
  toolCallIds,
  provider,
  title,
  tag,
  inspect,
}: {
  readonly cardId?: string;
  readonly toolCallIds: readonly string[];
  readonly provider: ToolIconKey;
  readonly title: string;
  readonly tag?: ReactNode;
  readonly inspect: InspectorTarget;
}) {
  return (
    <ResultCard
      id={cardId}
      toolCallIds={toolCallIds}
      provider={provider}
      title={title}
      tag={tag}
      state="unsupported"
      note="Sourcecado cannot read this result. The raw record is in the inspector."
      footer={<InspectButton target={inspect} />}
    />
  );
}

/** A card that throws while it renders becomes an unsupported card, not a blank thread. */
export class CardBoundary extends Component<
  { readonly fallback: () => ReactNode; readonly children: ReactNode },
  { readonly failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? this.props.fallback() : this.props.children;
  }
}
