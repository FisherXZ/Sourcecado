import { Glyph } from "../generative/ResultCard";
import { Icon } from "../icons";

const STATES = [
  { id: "open", label: "Open" },
  { id: "in_conversation", label: "In conversation" },
  { id: "done", label: "Done" },
] as const;

/**
 * Sequence state as a tinted pill over a native select, plus Undo for the
 * last change. The Person File header and the Contacts preview share it; each
 * host keeps its own save path.
 */
export function SequencePill({
  label,
  value,
  disabled,
  onChange,
  onUndo,
}: {
  /** Accessible name of the select. */
  label: string;
  /** Saved state, or "" when outreach has not started. */
  value: string;
  disabled: boolean;
  onChange: (state: string) => void;
  /** Present only while the last change can be undone. */
  onUndo?: () => void;
}) {
  return (
    <span className="sequence-control">
      <label className={`pf-sequence is-${value || "none"}`}>
        <span className="pf-visually-hidden">{label}</span>
        <select
          value={value}
          disabled={disabled}
          aria-busy={disabled || undefined}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="" disabled>
            Not started
          </option>
          {STATES.map((state) => (
            <option key={state.id} value={state.id}>
              {state.label}
            </option>
          ))}
        </select>
        <Glyph name="chevron" />
      </label>
      {onUndo ? (
        <button
          type="button"
          className="pf-icon-btn"
          aria-label="Undo sequence change"
          title="Undo sequence change"
          disabled={disabled}
          onClick={onUndo}
        >
          <Icon name="reply" />
        </button>
      ) : null}
    </span>
  );
}
