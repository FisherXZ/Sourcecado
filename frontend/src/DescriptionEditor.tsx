import { useEffect, useRef, useState } from "react";

import {
  DescriptionSaveError,
  patchPersonDescription,
  type DescriptionSlot,
  type PersonDescription,
  type PersonDescriptions,
} from "./api";
import {
  clearDescriptionDraft,
  readDescriptionDraft,
  subscribeDescriptionDraft,
  writeDescriptionDraft,
} from "./descriptionDrafts";
import { Glyph } from "./generative/ResultCard";
import { Icon } from "./icons";

const LIMITS: Record<DescriptionSlot, number> = { general: 500, detailed: 8000 };
const SLOT_LABEL: Record<DescriptionSlot, string> = {
  general: "Short description",
  detailed: "Detailed description",
};
const SLOT_NOUN: Record<DescriptionSlot, string> = {
  general: "description",
  detailed: "detailed description",
};

function savedText(description: PersonDescription): string {
  return description.text ?? "";
}

/** How a description reads when not being edited.
 *  - assistant-owned and empty → the "Not summarized yet" placeholder
 *  - human-owned and empty → an intentional protected blank (em dash)
 *  - otherwise the text itself
 */
export function describeDescription(
  description: PersonDescription | undefined,
): { text: string; muted: boolean } {
  const text = (description?.text ?? "").trim();
  if (text) return { text: description?.text ?? "", muted: false };
  if (!description || description.authority === "assistant") {
    return { text: "Not summarized yet", muted: true };
  }
  return { text: "—", muted: true };
}

/**
 * The one editor for a description slot, mounted anywhere (person file or
 * Contacts). At rest it shows the saved text with its owner, or one "Add"
 * button when there is none; the textarea and Save / Cancel / Clear appear
 * only while editing. A human-owned slot offers the "let the assistant
 * maintain this" release. Its draft is shared across every mount of the same
 * person+slot and survives relaunch, so a stored draft opens the editor; a
 * failed save keeps the unsaved text in place.
 */
export function DescriptionEditor({
  personId,
  description,
  onSaved,
  onClose,
  rows = 3,
  startEditing = false,
}: {
  personId: string;
  description: PersonDescription;
  onSaved: (next: PersonDescriptions) => void;
  /** Optional: called after a successful save or a cancel, so an inline host
   *  (the Contacts cell) can collapse the editor. */
  onClose?: () => void;
  rows?: number;
  /** The host already chose to edit (the Contacts cell), so skip the rest view. */
  startEditing?: boolean;
}) {
  const slot = description.slot;
  const limit = LIMITS[slot];
  const base = savedText(description);

  const [draft, setDraft] = useState<string | null>(
    () => readDescriptionDraft(personId, slot)?.text ?? null,
  );
  const [editing, setEditing] = useState(startEditing);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const focusNext = useRef<"field" | "toggle" | null>(null);
  const [stale, setStale] = useState<boolean>(() => {
    const stored = readDescriptionDraft(personId, slot);
    return stored !== null && stored.baseVersion !== description.version;
  });

  // Keep every surface editing this slot in sync, and re-evaluate staleness
  // whenever the saved version moves underneath the draft.
  useEffect(() => {
    const sync = () => {
      const stored = readDescriptionDraft(personId, slot);
      setDraft(stored?.text ?? null);
      setStale(stored !== null && stored.baseVersion !== description.version);
    };
    sync();
    return subscribeDescriptionDraft(personId, slot, sync);
  }, [personId, slot, description.version]);

  const value = draft ?? base;
  const dirty = draft !== null && draft !== base;
  const overLimit = value.length > limit;
  // A stored draft is unsaved work, so it always shows in the editor.
  const open = editing || draft !== null;

  useEffect(() => {
    const target = focusNext.current === "field" ? field.current
      : focusNext.current === "toggle" ? toggle.current : null;
    focusNext.current = null;
    target?.focus();
  }, [open]);

  function begin() {
    focusNext.current = "field";
    setStatus(null);
    setEditing(true);
  }

  function close() {
    focusNext.current = "toggle";
    setEditing(false);
    onClose?.();
  }

  function edit(next: string) {
    setDraft(next);
    setArmed(false);
    setStatus(null);
    writeDescriptionDraft(personId, slot, {
      text: next,
      baseVersion: description.version,
    });
  }

  function cancel() {
    setDraft(null);
    setArmed(false);
    setStatus(null);
    clearDescriptionDraft(personId, slot);
    close();
  }

  function clear() {
    // Empty the field; Save then persists it as an intentional protected blank.
    edit("");
  }

  async function save() {
    if (busy || overLimit || !dirty) return;
    const submitted = value;
    setBusy(true);
    setStatus(null);
    try {
      const result = await patchPersonDescription(personId, slot, {
        text: submitted,
        expectedVersion: description.version,
      });
      // The textarea stays editable during the request, so the user may have
      // typed past what we sent. Only retire the draft when it still matches
      // the submitted text; otherwise keep the newer keystrokes and rebase
      // them onto the version we just wrote, so nothing is lost and the next
      // save is not falsely flagged stale (ticket #202 Done when #5).
      const pending = readDescriptionDraft(personId, slot);
      const savedVersion = result.descriptions[slot]?.version ?? description.version;
      if (pending === null || pending.text === submitted) {
        clearDescriptionDraft(personId, slot);
        setDraft(null);
        setStale(false);
        onSaved(result.descriptions);
        close();
      } else {
        writeDescriptionDraft(personId, slot, {
          text: pending.text,
          baseVersion: savedVersion,
        });
        onSaved(result.descriptions);
      }
    } catch (error) {
      // A failed save leaves the unsaved text intact (Done when #5).
      if (error instanceof DescriptionSaveError && error.status === 409) {
        setStatus("The saved copy changed. Review it, then save again.");
      } else if (error instanceof DescriptionSaveError && error.status === 422) {
        setStatus(`Too long. Keep it under ${limit} characters.`);
      } else {
        setStatus("Couldn’t save. Your text is kept — try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function release() {
    if (busy) return;
    setBusy(true);
    setStatus(null);
    try {
      const result = await patchPersonDescription(personId, slot, {
        release: true,
        expectedVersion: description.version,
      });
      onSaved(result.descriptions);
    } catch {
      setStatus("Couldn’t hand this back to the assistant. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const noun = SLOT_NOUN[slot];
  const owner = description.authority === "human" ? (
    <span className="description-authority is-human" title="Protected from assistant updates">
      Human-edited
    </span>
  ) : (
    <span className="description-authority" title="Maintained by the assistant">
      Assistant
    </span>
  );
  const sources = description.source_refs?.length ? (
    <details className="description-sources" aria-label="Description sources">
      <summary>Sources ({description.source_refs.length})</summary>
      {description.source_refs.map((source, index) => {
        const url = typeof source === "string" ? source : source.url;
        return url?.startsWith("https://mail.google.com/")
          ? <a key={index} href={url} target="_blank" rel="noreferrer">Source email {index + 1}</a>
          : <span key={index}>Saved source {index + 1}</span>;
      })}
    </details>
  ) : null;
  const statusLine = status ? (
    <p className="crm-alert description-status" role="status">
      <Glyph name="info" />
      {status}
    </p>
  ) : null;

  if (!open) {
    const empty = base.trim() === "";
    return (
      <div className="description-editor" data-unsaved={busy ? "true" : undefined}>
        {empty ? null : <p className="description-text">{base}</p>}
        <div className="description-rest">
          {empty ? (
            <button ref={toggle} type="button" className="description-add" onClick={begin}>
              <Icon name="add" />
              Add {noun}
            </button>
          ) : null}
          {/* An empty assistant slot shows only the Add button. */}
          {empty && description.authority !== "human" ? null : owner}
          {description.authority === "human" ? (
            <button
              type="button"
              className="description-icon-button"
              aria-label="Let the assistant maintain this"
              title="Let the assistant maintain this"
              onClick={() => void release()}
              disabled={busy}
            >
              <Icon name="assistant" />
            </button>
          ) : null}
          {empty ? null : (
            <button
              ref={toggle}
              type="button"
              className="description-icon-button description-edit"
              aria-label={`Edit ${noun}`}
              title={`Edit ${noun}`}
              onClick={begin}
            >
              <Icon name="edit" />
            </button>
          )}
        </div>
        {sources}
        {statusLine}
      </div>
    );
  }

  // The counter appears only once the text nears the limit.
  const nearLimit = value.length >= limit * 0.8;

  return (
    <div
      className="description-editor is-editing"
      data-unsaved={dirty || busy ? "true" : undefined}
      onKeyDown={(event) => {
        if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
          event.preventDefault();
          void save();
          return;
        }
        if (event.key !== "Escape" || busy) return;
        // Escape cancels an unchanged edit; changed text needs a second Escape.
        event.preventDefault();
        event.stopPropagation();
        if (!dirty || armed) cancel();
        else setArmed(true);
      }}
    >
      {stale ? (
        <p className="crm-note description-stale" role="status">
          <Glyph name="info" />
          The saved copy changed since you started this draft. Saving replaces it.
        </p>
      ) : null}
      <textarea
        ref={field}
        className="description-textarea"
        aria-label={SLOT_LABEL[slot]}
        placeholder={slot === "general" ? "Add a short description" : "Add detailed notes"}
        rows={rows}
        value={value}
        onChange={(event) => edit(event.target.value)}
      />
      <div className="description-editor-foot">
        <button
          type="button"
          className="crm-btn is-ghost description-clear"
          title="Empty the text. Save keeps it blank."
          onClick={clear}
          disabled={busy || value.length === 0}
        >
          <Icon name="deny" />
          Clear
        </button>
        {armed && dirty ? (
          <span className="description-hint" role="status">Press Escape again to discard</span>
        ) : null}
        {nearLimit ? (
          <span className={`description-count${overLimit ? " is-over" : ""}`}>
            {value.length} / {limit}
          </span>
        ) : null}
        <button type="button" className="crm-btn is-ghost" onClick={cancel} disabled={busy}>
          <Icon name="close" />
          Cancel
        </button>
        <button
          type="button"
          className="crm-btn is-primary"
          title="Save (⌘Enter)"
          onClick={() => void save()}
          disabled={busy || overLimit || !dirty}
        >
          <Icon name="check" />
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
      {statusLine}
    </div>
  );
}
