import type { ContextChip } from "./messageContext";
import { CloseIcon, PersonIcon } from "./icons";
import { usePersonName } from "./personNames";

function ContextChipItem({
  chip,
  onRemove,
}: {
  readonly chip: ContextChip;
  readonly onRemove?: (chip: ContextChip) => void;
}) {
  // The label is the person's current Board name. It is never read from the message.
  const name = usePersonName(chip.id);
  return (
    <li className="sourcecado-context-chip">
      <span className="sourcecado-context-chip-icon">
        <PersonIcon />
      </span>
      <span className="sourcecado-context-chip-name">{name}</span>
      {onRemove ? (
        <button
          type="button"
          className="sourcecado-context-chip-remove"
          aria-label={`Remove ${name}`}
          onClick={() => onRemove(chip)}
        >
          <CloseIcon />
        </button>
      ) : null}
    </li>
  );
}

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
        <ContextChipItem key={`${chip.kind}:${chip.id}`} chip={chip} onRemove={onRemove} />
      ))}
    </ul>
  );
}
