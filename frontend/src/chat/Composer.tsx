import { ComposerPrimitive, useAui, useAuiState } from "@assistant-ui/react";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
  type SyntheticEvent,
} from "react";

import type { ActivePerson } from "../api";
import { ComposerFileChips } from "./AttachmentChips";
import { formatBytes } from "./attachments";
import { ConnectionChip } from "./ConnectionChip";
import { ContextChips } from "./ContextChips";
import { ContextMenu } from "./ContextMenu";
import { FileIcon, SendIcon } from "./icons";
import { useScrollActiveIntoView } from "./MenuRow";
import { withContext, type ContextChip } from "./messageContext";
import {
  matchSkills,
  skillOptionId,
  slashQuery,
  SlashMenu,
  useSkillCatalog,
} from "./SlashMenu";
import { useComposerFiles } from "./useComposerFiles";

/** A pasted picture has no name worth keeping. */
function pastedName(file: File, index: number): string {
  const extension = file.type.split("/")[1]?.replace("jpeg", "jpg") || "png";
  return `Pasted image${index > 0 ? ` ${index + 1}` : ""}.${extension}`;
}

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
  const attachments = useComposerFiles();
  const picker = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const blocked = attachments.blockedReason !== null;

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

  // The chips and the attached files are written into the text at send time,
  // whether the director presses Enter (a form submit) or clicks Send. With
  // none attached the library sends exactly as it did before. A file that is
  // still processing, or that failed, holds the message back.
  function sendWithContext(event: SyntheticEvent) {
    if (chips.length === 0 && attachments.files.length === 0) return;
    event.preventDefault();
    if (blocked) return;
    if (!aui.composer.getState().canSend) return;
    aui.composer.setText(
      withContext(aui.composer.getState().text, chips, attachments.readyIds),
    );
    void aui.composer.send();
    setChips([]);
    attachments.clear();
  }

  const carriesFiles = (event: DragEvent) =>
    Array.from(event.dataTransfer?.types ?? []).includes("Files");

  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const data = event.clipboardData;
    const pictures = Array.from(data?.files ?? []).filter((file) =>
      file.type.startsWith("image/"),
    );
    // Pasted text stays text, even when the clipboard also holds a picture of it.
    if (pictures.length === 0 || data.getData("text/plain") !== "") return;
    event.preventDefault();
    void attachments.add(
      pictures.map(
        (file, index) => new File([file], pastedName(file, index), { type: file.type }),
      ),
    );
  }

  const limits = attachments.limits;
  const filesFull = limits !== null && attachments.files.length >= limits.max_files;

  return (
    <>
    <ConnectionChip />
    <ComposerPrimitive.Root
      className="sourcecado-composer"
      data-dragging={dragging || undefined}
      onSubmit={sendWithContext}
      onDragEnter={(event) => {
        if (!carriesFiles(event)) return;
        dragDepth.current += 1;
        setDragging(true);
      }}
      onDragOver={(event) => {
        if (!carriesFiles(event)) return;
        // Without this the browser opens the file instead of dropping it here.
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={(event) => {
        if (!carriesFiles(event)) return;
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (dragDepth.current === 0) setDragging(false);
      }}
      onDrop={(event) => {
        if (!carriesFiles(event)) return;
        event.preventDefault();
        dragDepth.current = 0;
        setDragging(false);
        void attachments.add(Array.from(event.dataTransfer.files));
        input.current?.focus();
      }}
    >
      <ContextChips
        chips={chips}
        onRemove={(chip) => {
          setChips((current) => current.filter((item) => item.id !== chip.id));
          // The button that held focus is gone. Keep focus in the box.
          input.current?.focus();
        }}
      />
      <ComposerFileChips
        files={attachments.files}
        onRemove={(file) => {
          attachments.remove(file.key);
          // The button that held focus is gone. Keep focus in the box.
          input.current?.focus();
        }}
      />
      {attachments.blockedReason ?? attachments.notice ? (
        <p className="sourcecado-attachment-status" role="status">
          {attachments.blockedReason ?? attachments.notice}
        </p>
      ) : null}
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
        onPaste={onPaste}
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
          onOpen={() => {
            setDismissedAt(text);
            void attachments.refreshLimits();
          }}
          fileOption={{
            detail: filesFull
              ? `Up to ${limits.max_files} files per message. Remove one to add another.`
              : limits
                ? `Up to ${limits.max_files} files, ${formatBytes(limits.max_bytes)} each${
                    limits.images ? "" : ". No images with this model."
                  }`
                : "PDF, images, text, Markdown or CSV",
            full: filesFull,
            onChoose: () => picker.current?.click(),
          }}
          onAttach={(chip) =>
            setChips((current) =>
              current.some((item) => item.id === chip.id) ? current : [...current, chip],
            )
          }
          onPicked={() => input.current?.focus()}
        />
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          aria-label="Choose files to attach"
          accept={limits?.accept || undefined}
          onChange={(event) => {
            const picked = Array.from(event.currentTarget.files ?? []);
            // Choosing the same file again must still fire a change.
            event.currentTarget.value = "";
            void attachments.add(picked);
          }}
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
            aria-disabled={blocked || undefined}
            onClick={sendWithContext}
          >
            <span className="sourcecado-composer-action-visual" aria-hidden="true">
              <SendIcon />
            </span>
          </ComposerPrimitive.Send>
        )}
      </div>
      {dragging ? (
        <div className="sourcecado-composer-drop" aria-hidden="true">
          <FileIcon />
          Drop files to attach
        </div>
      ) : null}
    </ComposerPrimitive.Root>
    </>
  );
}
