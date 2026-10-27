import { ComposerPrimitive, useAui, useAuiState } from "@assistant-ui/react";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type SyntheticEvent,
} from "react";

import type { ActivePerson } from "../api";
import { ContextChips } from "./ContextChips";
import { ContextMenu } from "./ContextMenu";
import { SendIcon } from "./icons";
import { useScrollActiveIntoView } from "./MenuRow";
import { withContext, type ContextChip } from "./messageContext";
import {
  matchSkills,
  skillOptionId,
  slashQuery,
  SlashMenu,
  useSkillCatalog,
} from "./SlashMenu";

export function Composer({
  initialDraft,
  onDraftChange,
  boundPerson = null,
}: {
  readonly initialDraft: string;
  readonly onDraftChange: (draft: string) => void;
  /** Set when the conversation is about one person. */
  readonly boundPerson?: ActivePerson | null;
}) {
  const aui = useAui();
  const running = useAuiState((state) => state.thread.isRunning);
  const text = useAuiState((state) => state.composer.text);
  const hydrated = useRef(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const menuId = useId();
  const hintsId = useId();
  const [chips, setChips] = useState<readonly ContextChip[]>([]);
  const [dismissedAt, setDismissedAt] = useState<string | null>(null);
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    aui.composer.setText(initialDraft);
  }, [aui, initialDraft]);

  const query = slashQuery(text);
  // Leaving the "/" word forgets an Escape, so a later "/" opens the menu again.
  useEffect(() => {
    if (query === null) setDismissedAt(null);
  }, [query]);
  const slashOpen = query !== null && dismissedAt !== text;
  const { catalog, retry } = useSkillCatalog(slashOpen);
  const skills = catalog.status === "loaded" ? matchSkills(catalog.skills, query ?? "") : [];
  const activeSkill = skills[Math.min(active, skills.length - 1)];
  useScrollActiveIntoView(
    slashOpen && activeSkill ? skillOptionId(menuId, skills.indexOf(activeSkill)) : undefined,
  );

  function insertSkill(name: string) {
    const next = `/${name} `;
    aui.composer.setText(next);
    onDraftChange(next);
    input.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (!slashOpen || event.nativeEvent.isComposing) return;
    if (event.key === "Escape") {
      event.preventDefault();
      setDismissedAt(text);
    } else if (skills.length > 0 && event.key === "ArrowDown") {
      event.preventDefault();
      setActive((current) => (current + 1) % skills.length);
    } else if (skills.length > 0 && event.key === "ArrowUp") {
      event.preventDefault();
      setActive((current) => (current - 1 + skills.length) % skills.length);
    } else if (
      activeSkill &&
      (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey))
    ) {
      // preventDefault also stops the library from sending on this Enter.
      event.preventDefault();
      insertSkill(activeSkill.name);
    }
  }

  // The chips are written into the text at send time, whether the director
  // presses Enter (a form submit) or clicks Send. With none attached the
  // library sends exactly as it did before.
  function sendWithContext(event: SyntheticEvent) {
    if (chips.length === 0) return;
    event.preventDefault();
    if (!aui.composer.getState().canSend) return;
    aui.composer.setText(withContext(aui.composer.getState().text, chips));
    void aui.composer.send();
    setChips([]);
  }

  return (
    <ComposerPrimitive.Root className="sourcecado-composer" onSubmit={sendWithContext}>
      <ContextChips
        chips={chips}
        onRemove={(chip) => {
          setChips((current) => current.filter((item) => item.id !== chip.id));
          // The button that held focus is gone. Keep focus in the box.
          input.current?.focus();
        }}
      />
      <ComposerPrimitive.Input
        ref={input}
        aria-label="Message Sourcecado"
        aria-describedby={hintsId}
        placeholder={
          running
            ? "Queue another message"
            : "Ask Sourcecado about this week’s sourcing work"
        }
        rows={2}
        // Escape closes the skill menu first. It stops a run only when no menu is open.
        cancelOnEscape={!slashOpen}
        onChange={(event) => {
          setActive(0);
          onDraftChange(event.currentTarget.value);
        }}
        onKeyDown={onKeyDown}
        {...(slashOpen
          ? {
              "aria-haspopup": "listbox" as const,
              "aria-expanded": true,
              "aria-controls": menuId,
              "aria-activedescendant": activeSkill
                ? skillOptionId(menuId, skills.indexOf(activeSkill))
                : undefined,
            }
          : {})}
      />
      {slashOpen ? (
        <SlashMenu
          id={menuId}
          catalog={catalog}
          query={query ?? ""}
          items={skills}
          activeIndex={activeSkill ? skills.indexOf(activeSkill) : -1}
          onPick={(skill) => insertSkill(skill.name)}
          onRetry={retry}
        />
      ) : null}
      <div className="sourcecado-composer-footer">
        <ContextMenu
          attached={chips}
          boundPerson={boundPerson}
          slashOpen={slashOpen}
          onOpen={() => setDismissedAt(text)}
          onAttach={(chip) =>
            setChips((current) =>
              current.some((item) => item.id === chip.id) ? current : [...current, chip],
            )
          }
          onPicked={() => input.current?.focus()}
        />
        <p className="sourcecado-composer-hints" id={hintsId}>
          <span>
            <kbd>⏎</kbd> {running ? "queue" : "send"}
          </span>
          <span>
            <kbd>⇧⏎</kbd> newline
          </span>
        </p>
        {running ? (
          <ComposerPrimitive.Cancel
            className="sourcecado-composer-action"
            aria-label="Stop run"
          >
            <span className="sourcecado-composer-action-visual" aria-hidden="true">
              <span className="sourcecado-stop-icon" />
            </span>
          </ComposerPrimitive.Cancel>
        ) : (
          <ComposerPrimitive.Send
            className="sourcecado-composer-action"
            aria-label="Send message"
            onClick={sendWithContext}
          >
            <span className="sourcecado-composer-action-visual" aria-hidden="true">
              <SendIcon />
            </span>
          </ComposerPrimitive.Send>
        )}
      </div>
    </ComposerPrimitive.Root>
  );
}
