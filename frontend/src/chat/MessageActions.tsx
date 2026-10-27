import {
  useAui,
  useAuiState,
  type ThreadMessage,
  type ToolCallMessagePart,
} from "@assistant-ui/react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { usePopulateComposer } from "./ComposerDraftContext";
import { CheckIcon, EditIcon, Icon, RetryIcon } from "./icons";
import { retryBlock } from "./retryPolicy";

const COPIED_MS = 1800;

const CopyIcon = () => (
  <Icon>
    <rect x="7.5" y="7.5" width="8" height="8" rx="1.8" />
    <path d="M12.5 7.5V6A1.8 1.8 0 0 0 10.7 4.2H6A1.8 1.8 0 0 0 4.2 6v4.7A1.8 1.8 0 0 0 6 12.5h1.5" />
  </Icon>
);

const ThumbUpIcon = () => (
  <Icon>
    <path d="M4.5 9H7v8H4.5z" />
    <path d="M7 9.2 9.4 4c1.3-.1 2.4 1 2.2 2.3L11.2 8.6h3.7c1.1 0 1.9 1 1.6 2.1l-1.1 4.7c-.2.9-1 1.6-1.9 1.6H7" />
  </Icon>
);

const ThumbDownIcon = () => (
  <Icon>
    <g transform="translate(0 20) scale(1 -1)">
      <path d="M4.5 9H7v8H4.5z" />
      <path d="M7 9.2 9.4 4c1.3-.1 2.4 1 2.2 2.3L11.2 8.6h3.7c1.1 0 1.9 1 1.6 2.1l-1.1 4.7c-.2.9-1 1.6-1.9 1.6H7" />
    </g>
  </Icon>
);

function textOf(content: ThreadMessage["content"]): string {
  return content
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("\n");
}

/**
 * One icon button. The name is for assistive technology and the tooltip is for
 * everyone else, so they say the same thing.
 *
 * A button that cannot act stays in the tab order with `aria-disabled`, so a
 * keyboard user can reach it and read why. The reason is the tooltip.
 */
function ActionButton({
  label,
  reason,
  pressed,
  state,
  tip,
  onClick,
  children,
}: {
  readonly label: string;
  readonly reason?: string | null;
  readonly pressed?: boolean;
  readonly state?: "copied" | "failed";
  readonly tip?: string;
  readonly onClick: () => void;
  readonly children: ReactNode;
}) {
  const unavailable = reason ? `${label} unavailable. ${reason}` : null;
  return (
    <button
      type="button"
      className="sourcecado-message-action"
      aria-label={unavailable ?? label}
      aria-disabled={unavailable ? true : undefined}
      aria-pressed={pressed}
      data-tooltip={unavailable ?? tip ?? label}
      data-state={state}
      onClick={unavailable ? undefined : onClick}
    >
      {children}
    </button>
  );
}

type CopyState = "idle" | "copied" | "failed";

function CopyAction({ text }: { readonly text: string }) {
  const [copy, setCopy] = useState<CopyState>("idle");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copyText = async () => {
    let next: CopyState = "copied";
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      next = "failed";
    }
    setCopy(next);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopy("idle"), COPIED_MS);
  };

  return (
    <>
      <ActionButton
        label="Copy response"
        tip={
          copy === "copied"
            ? "Copied"
            : copy === "failed"
              ? "Copy failed"
              : "Copy response"
        }
        state={copy === "idle" ? undefined : copy}
        onClick={() => void copyText()}
      >
        {copy === "copied" ? <CheckIcon /> : <CopyIcon />}
      </ActionButton>
      <span className="sr-only" aria-live="polite">
        {copy === "copied" ? "Copied" : copy === "failed" ? "Copy failed" : ""}
      </span>
    </>
  );
}

function RetryAction({
  tools,
  messageState,
}: {
  readonly tools: readonly ToolCallMessagePart[];
  readonly messageState: unknown;
}) {
  const aui = useAui();
  // The operator message this answer replied to. Sent as it was written,
  // including any attached-context lines, so the new turn sees the same input.
  const prompt = useAuiState((state) => {
    for (let at = state.message.index - 1; at >= 0; at -= 1) {
      const earlier = state.thread.messages[at];
      if (earlier?.role === "user") return textOf(earlier.content);
    }
    return "";
  });
  const reason =
    prompt.trim() === ""
      ? "There is no earlier message to send again."
      : retryBlock(tools, messageState);

  return (
    <ActionButton
      label="Retry"
      reason={reason}
      // thread().append starts the turn the way the composer does, without
      // touching a draft the operator is typing.
      onClick={() => aui.thread().append(prompt)}
    >
      <RetryIcon />
    </ActionButton>
  );
}

/**
 * Thumbs. There is no endpoint that stores feedback, so this is the
 * operator's own mark on this screen. It is not sent anywhere, and the tooltip
 * says so once it is set.
 */
function FeedbackActions() {
  const [mark, setMark] = useState<"up" | "down" | null>(null);
  const toggle = (next: "up" | "down") =>
    setMark((current) => (current === next ? null : next));
  return (
    <>
      <ActionButton
        label="Good response"
        tip={mark === "up" ? "Marked good. Kept on this screen only." : "Good response"}
        pressed={mark === "up"}
        onClick={() => toggle("up")}
      >
        <ThumbUpIcon />
      </ActionButton>
      <ActionButton
        label="Bad response"
        tip={mark === "down" ? "Marked bad. Kept on this screen only." : "Bad response"}
        pressed={mark === "down"}
        onClick={() => toggle("down")}
      >
        <ThumbDownIcon />
      </ActionButton>
    </>
  );
}

/**
 * The bar under a finished answer. Retry belongs to the newest answer only,
 * because it starts a new turn at the end of the thread.
 */
export function AssistantActions({
  prose,
  tools,
  messageState,
}: {
  readonly prose: string;
  readonly tools: readonly ToolCallMessagePart[];
  readonly messageState: unknown;
}) {
  const isLast = useAuiState((state) => state.message.isLast);
  return (
    <div className="sourcecado-message-actions" role="group" aria-label="Response actions">
      <CopyAction text={prose} />
      {isLast ? <RetryAction tools={tools} messageState={messageState} /> : null}
      <FeedbackActions />
    </div>
  );
}

function focusComposer(length: number) {
  window.requestAnimationFrame(() => {
    const input = document.querySelector<HTMLTextAreaElement>(
      ".sourcecado-composer textarea",
    );
    if (!input) return;
    input.focus();
    input.setSelectionRange(length, length);
  });
}

/**
 * Edit on the operator's own message. It puts the text back in the composer
 * and stops there: the operator reads it, changes it and sends it.
 *
 * It will not overwrite a different draft, because that would lose what the
 * operator typed.
 */
export function EditMessageAction() {
  const populate = usePopulateComposer();
  const text = useAuiState((state) => textOf(state.message.content));
  // A boolean, so a keystroke re-renders this only when the answer changes.
  const otherDraft = useAuiState((state) => {
    const draft = state.thread.composer.text;
    return draft.trim() !== "" && draft !== textOf(state.message.content);
  });
  if (!populate) return null;
  const reason = otherDraft ? "Send or clear the message you are writing first." : null;
  return (
    <div className="sourcecado-user-actions">
      <div className="sourcecado-message-actions" role="group" aria-label="Message actions">
        <ActionButton
          label="Edit message"
          reason={reason}
          onClick={() => {
            populate(text);
            focusComposer(text.length);
          }}
        >
          <EditIcon />
        </ActionButton>
      </div>
    </div>
  );
}
