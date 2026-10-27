import type { ContextChip } from "./messageContext";
import { CloseIcon, PersonIcon } from "./icons";

/**
 * The chips attached to a message: removable above the composer box, read-only
 * on a sent message or a queued one.
 */
export function ContextChips({
  chips,
  onRemove,
}: {
  readonly chips: readonly ContextChip[];
  readonly onRemove?: (chip: ContextChip) => void;
}) {
  if (chips.length === 0) return null;
  return (
    <ul className="sourcecado-context-chips" aria-label="Attached context">
      {chips.map((chip) => (
        <li className="sourcecado-context-chip" key={`${chip.kind}:${chip.id}`}>
          <span className="sourcecado-context-chip-icon">
            <PersonIcon />
          </span>
          <span className="sourcecado-context-chip-name">{chip.name}</span>
          {onRemove ? (
            <button
              type="button"
              className="sourcecado-context-chip-remove"
              aria-label={`Remove ${chip.name}`}
              onClick={() => onRemove(chip)}
            >
              <CloseIcon />
            </button>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
