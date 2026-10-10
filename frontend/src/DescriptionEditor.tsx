import { useEffect, useState } from "react";

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

const LIMITS: Record<DescriptionSlot, number> = { general: 500, detailed: 8000 };
const SLOT_LABEL: Record<DescriptionSlot, string> = {
  general: "Short description",
  detailed: "Detailed description",
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
 * Contacts). Save / Cancel / Clear plus the "let the assistant maintain this"
 * release. Its draft is shared across every mount of the same person+slot and
 * survives relaunch; a failed save keeps the unsaved text in place.
 */
export function DescriptionEditor({
  personId,
  description,
  onSaved,
  onClose,
  rows = 3,
}: {
  personId: string;
  description: PersonDescription;
  onSaved: (next: PersonDescriptions) => void;
  /** Optional: called after a successful save or a cancel, so an inline host
   *  (the Contacts cell) can collapse the editor. */
  onClose?: () => void;
  rows?: number;
}) {
  const slot = description.slot;
  const limit = LIMITS[slot];
  const base = savedText(description);

  const [draft, setDraft] = useState<string | null>(
    () => readDescriptionDraft(personId, slot)?.text ?? null,
  );
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
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

  function edit(next: string) {
    setDraft(next);
    setStatus(null);
    writeDescriptionDraft(personId, slot, {
      text: next,
      baseVersion: description.version,
    });
  }

  function cancel() {
    setDraft(null);
    setStatus(null);
    clearDescriptionDraft(personId, slot);
    onClose?.();
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
        onClose?.();
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

  return (
    <div className="description-editor">
      {stale ? (
        <p className="description-stale" role="status">
          The saved copy changed since you started this draft. Saving replaces it.
        </p>
      ) : null}
      <textarea
        className="description-textarea"
        aria-label={SLOT_LABEL[slot]}
        rows={rows}
        value={value}
        onChange={(event) => edit(event.target.value)}
      />
      <div className="description-editor-meta">
        <span className={`description-count${overLimit ? " is-over" : ""}`}>
          {value.length} / {limit}
        </span>
        <span className="description-authority">
          {description.authority === "human"
            ? "Human-written · protected"
            : "Assistant-maintained"}
        </span>
      </div>
      <div className="description-actions">
        <button
          type="button"
          onClick={() => void save()}
          disabled={busy || overLimit || !dirty}
        >
          {busy ? "Saving…" : "Save"}
        </button>
        <button type="button" onClick={cancel} disabled={busy || !dirty}>
          Cancel
        </button>
        <button type="button" onClick={clear} disabled={busy || value.length === 0}>
          Clear
        </button>
        {description.authority === "human" ? (
          <button
            type="button"
            className="description-release"
            onClick={() => void release()}
            disabled={busy}
          >
            Let the assistant maintain this
          </button>
        ) : null}
      </div>
      {status ? (
        <p className="description-status" role="status">
          {status}
        </p>
      ) : null}
    </div>
  );
}
