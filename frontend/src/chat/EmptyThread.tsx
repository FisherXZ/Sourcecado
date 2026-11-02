import { useEffect, useMemo, useState, type ReactNode } from "react";

import { getBoard, getConnectors, getSkills, hasToken } from "../api";
import {
  buildPrompts,
  greetingFor,
  type PromptChip,
  type PromptIcon,
  type PromptSources,
} from "./emptyPrompts";

const ICONS: Readonly<Record<PromptIcon, ReactNode>> = {
  target: (
    <>
      <circle cx="10" cy="10" r="6.5" />
      <circle cx="10" cy="10" r="2.5" />
    </>
  ),
  people: (
    <>
      <circle cx="7.5" cy="7" r="2.75" />
      <path d="M2.5 16c.4-2.8 2.4-4.5 5-4.5s4.6 1.7 5 4.5" />
      <path d="M12.5 4.6a2.6 2.6 0 0 1 0 4.8M14 11.8c1.9.5 3.1 2 3.5 4.2" />
    </>
  ),
  plug: (
    <>
      <path d="M7 3v4M13 3v4M5 7h10v2.5a5 5 0 0 1-10 0z" />
      <path d="M10 14.5V17" />
    </>
  ),
  skill: <path d="M11 2.5 4.5 11H9l-1 6.5L15.5 9H11z" />,
};

function PromptIconTile({ icon }: { readonly icon: PromptIcon }) {
  return (
    <span className="sourcecado-suggestion-icon" aria-hidden="true">
      <svg viewBox="0 0 20 20" focusable="false">
        {ICONS[icon]}
      </svg>
    </span>
  );
}

/**
 * Read the three sources at once and keep each answer on its own. A chip shows
 * as soon as its source answers. A source that fails or never answers adds no
 * chip and holds back no other.
 */
function usePromptChips(): readonly PromptChip[] {
  const [sources, setSources] = useState<PromptSources>({
    board: null,
    connectors: null,
    skills: null,
  });
  useEffect(() => {
    if (!hasToken()) return;
    let active = true;
    // Promise.resolve().then also catches a read that throws on the spot.
    const read = <T,>(load: () => Promise<T>, keep: (value: T) => Partial<PromptSources>) => {
      void Promise.resolve()
        .then(load)
        .then((value) => {
          const answer = keep(value);
          if (active) setSources((current) => ({ ...current, ...answer }));
        })
        .catch(() => {});
    };
    read(() => getBoard(), (board) => ({ board }));
    read(() => getConnectors(), ({ connectors }) => ({ connectors }));
    read(() => getSkills(), ({ skills }) => ({ skills }));
    return () => {
      active = false;
    };
  }, []);
  return useMemo(() => buildPrompts(sources), [sources]);
}

/** The greeting above the composer. The owl shows on wide windows only. */
export function EmptyGreeting() {
  return (
    <h2 className="sourcecado-empty-greeting">
      <span className="sourcecado-empty-owl" aria-hidden="true" />
      {greetingFor(new Date())}
    </h2>
  );
}

/** Prompt chips under the composer. A click fills the composer. It never sends. */
export function EmptyPrompts({
  onPick,
}: {
  readonly onPick: (prompt: string) => void;
}) {
  const chips = usePromptChips();
  if (chips.length === 0) return null;
  return (
    <div className="sourcecado-suggestions" role="group" aria-label="Suggested prompts">
      {chips.map((chip) => (
        <button
          key={chip.id}
          type="button"
          className="sourcecado-suggestion"
          title={chip.label}
          onClick={(event) => {
            onPick(chip.prompt);
            // Leave the caret in the composer so the operator can edit the
            // prompt and press Enter to send it themselves.
            event.currentTarget
              .closest(".sourcecado-composer-zone")
              ?.querySelector("textarea")
              ?.focus();
          }}
        >
          <PromptIconTile icon={chip.icon} />
          <span className="sourcecado-suggestion-label">{chip.label}</span>
        </button>
      ))}
    </div>
  );
}
