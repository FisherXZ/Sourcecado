import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import { ActionIcon } from "./ProcessIcons";
import { formatElapsed } from "./processState";
import {
  firstReasoningLine,
  thinkingElapsedMs,
  thoughtLabel,
  type ReasoningTiming,
} from "./thinking";

/** A 16px spark. Decoration only: the row says its state in words. */
function SparkIcon() {
  return (
    <svg
      className="sourcecado-process-icon sourcecado-thinking-icon"
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
      <path d="M8 2v3.2M8 10.8V14M2 8h3.2M10.8 8H14M4 4l2.1 2.1M9.9 9.9 12 12M12 4 9.9 6.1M6.1 9.9 4 12" />
    </svg>
  );
}

/**
 * The thinking row (#225): the model's raw reasoning, labeled as model
 * reasoning. It is open while the model thinks, with the text streaming under
 * the row and a live count. When the answer or a tool call starts it closes
 * to "Thought for 12s" and the first line. The operator's own open or close
 * choice wins from then on, so `open` is owned by the caller and survives the
 * row moving between the single-row and receipt layouts.
 *
 * It is a different kind of row from a tool step. It has no state icon and no
 * success mark, and nothing in it is a record that an action happened.
 */
export function ThinkingRow({
  text,
  thinking,
  timing,
  chosen,
  onChoose,
}: {
  readonly text: string;
  readonly thinking: boolean;
  readonly timing: ReasoningTiming;
  readonly chosen: boolean | null;
  readonly onChoose: (open: boolean) => void;
}) {
  const open = chosen ?? thinking;
  const bodyId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const [, tick] = useState(0);

  // The live count moves once a second while the model thinks.
  useEffect(() => {
    if (!thinking) return;
    const id = window.setInterval(() => tick((value) => value + 1), 1000);
    return () => window.clearInterval(id);
  }, [thinking]);

  // Follow the newest text while the model thinks, unless the operator has
  // scrolled up to read an earlier part.
  useEffect(() => {
    const body = bodyRef.current;
    if (open && thinking && body && followRef.current) {
      body.scrollTop = body.scrollHeight;
    }
  }, [open, thinking, text]);

  const elapsedMs = thinkingElapsedMs(timing, Date.now());
  const line = firstReasoningLine(text);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Escape" || !open) return;
    event.stopPropagation();
    onChoose(false);
    toggleRef.current?.focus();
  };

  return (
    <div
      className={`sourcecado-thinking${thinking ? " sourcecado-thinking-live" : ""}`}
      onKeyDown={onKeyDown}
    >
      <button
        ref={toggleRef}
        type="button"
        className="sourcecado-step-toggle sourcecado-thinking-toggle"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => onChoose(!open)}
      >
        <SparkIcon />
        <span className="sourcecado-step-label sourcecado-thinking-label">
          {thinking ? "Thinking…" : thoughtLabel(elapsedMs)}
          {thinking && elapsedMs !== null ? (
            <>
              {" "}
              <span className="sourcecado-step-elapsed">
                {formatElapsed(elapsedMs, true)}
              </span>
            </>
          ) : null}
        </span>
        <span className="sourcecado-step-state sourcecado-thinking-tag">
          Model reasoning
        </span>
        <ActionIcon name="chevron" />
        {!open && line ? (
          <span className="sourcecado-thinking-line">{line}</span>
        ) : null}
      </button>
      {open ? (
        <div
          id={bodyId}
          ref={bodyRef}
          className="sourcecado-thinking-body"
          role="group"
          aria-label="Model reasoning"
          tabIndex={0}
          onScroll={(event) => {
            const body = event.currentTarget;
            followRef.current =
              body.scrollHeight - body.scrollTop - body.clientHeight < 24;
          }}
        >
          {text}
        </div>
      ) : null}
    </div>
  );
}
