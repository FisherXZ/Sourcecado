import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Glyph } from "../generative/ResultCard";
import { Icon } from "../icons";

/** Not started is shown, never chosen: a sequence starts with outreach. */
const STATES = [
  { id: "", label: "Not started" },
  { id: "open", label: "Open" },
  { id: "in_conversation", label: "In conversation" },
  { id: "done", label: "Done" },
] as const;
const choosable = (index: number) => STATES[index]?.id !== "";

/**
 * Sequence state as a tinted pill that opens a listbox of the four states,
 * plus Undo for the last change. The Person File header and the Contacts
 * preview share it; each host keeps its own save path.
 */
export function SequencePill({
  label,
  value,
  disabled,
  onChange,
  onUndo,
}: {
  /** Accessible name of the pill and its list. */
  label: string;
  /** Saved state, or "" when outreach has not started. */
  value: string;
  disabled: boolean;
  onChange: (state: string) => void;
  /** Present only while the last change can be undone. */
  onUndo?: () => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const current = Math.max(
    0,
    STATES.findIndex((state) => state.id === value),
  );
  const [active, setActive] = useState(current);
  const root = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (!open) return;
    list.current?.focus();
    const outside = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open]);
  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  function show() {
    setActive(choosable(current) ? current : 1);
    setOpen(true);
  }
  function close() {
    setOpen(false);
    button.current?.focus();
  }
  function choose(index: number) {
    if (!choosable(index)) return;
    close();
    if (STATES[index].id !== value) onChange(STATES[index].id);
  }
  function step(from: number, by: number) {
    let next = from;
    do next = (next + by + STATES.length) % STATES.length;
    while (!choosable(next));
    return next;
  }
  function onListKey(event: KeyboardEvent<HTMLUListElement>) {
    const keys: Record<string, () => void> = {
      ArrowDown: () => setActive(step(active, 1)),
      ArrowUp: () => setActive(step(active, -1)),
      Home: () => setActive(step(STATES.length - 1, 1)),
      End: () => setActive(STATES.length - 1),
      Enter: () => choose(active),
      " ": () => choose(active),
      Escape: close,
      Tab: () => setOpen(false),
    };
    const action = keys[event.key];
    if (!action) return;
    if (event.key !== "Tab") event.preventDefault();
    // Escape closes this list only, not the preview around it.
    event.stopPropagation();
    action();
  }

  return (
    <span className="sequence-control" ref={root}>
      <button
        ref={button}
        type="button"
        className={`pf-sequence is-${value || "none"}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${id}-list` : undefined}
        // aria-disabled, not disabled, so focus stays on the pill while a
        // change saves.
        aria-busy={disabled || undefined}
        aria-disabled={disabled || undefined}
        onClick={() => {
          if (disabled) return;
          if (open) setOpen(false);
          else show();
        }}
        onKeyDown={(event) => {
          if (disabled || (event.key !== "ArrowDown" && event.key !== "ArrowUp")) return;
          event.preventDefault();
          show();
        }}
      >
        <span className="pf-visually-hidden">{label}: </span>
        {STATES[current].label}
        <Glyph name="chevron" />
      </button>
      {open ? (
        <ul
          ref={list}
          id={`${id}-list`}
          role="listbox"
          aria-label={label}
          tabIndex={-1}
          className="sequence-menu"
          aria-activedescendant={`${id}-${active}`}
          onKeyDown={onListKey}
          onBlur={(event) => {
            // Tabbing away closes the list. A click outside is handled by the
            // mousedown listener, since Safari does not focus clicked buttons.
            const next = event.relatedTarget as Node | null;
            if (next && !root.current?.contains(next)) setOpen(false);
          }}
        >
          {STATES.map((state, index) => (
            <li
              key={state.id || "none"}
              id={`${id}-${index}`}
              role="option"
              aria-selected={index === current}
              aria-disabled={!choosable(index) || undefined}
              className={`sequence-option is-${state.id || "none"}${index === active ? " is-active" : ""}`}
              onMouseEnter={() => choosable(index) && setActive(index)}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(index)}
            >
              <span className="sequence-option-check" aria-hidden="true">
                {index === current ? <Icon name="check" /> : null}
              </span>
              <span className="sequence-option-dot" aria-hidden="true" />
              {state.label}
            </li>
          ))}
        </ul>
      ) : null}
      {onUndo ? (
        <button
          type="button"
          className="sequence-undo"
          aria-label="Undo sequence change"
          title="Undo sequence change"
          disabled={disabled}
          onClick={onUndo}
        >
          <Icon name="reply" />
          Undo
        </button>
      ) : null}
    </span>
  );
}
