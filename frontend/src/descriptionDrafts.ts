/**
 * Unsaved description drafts, one shared buffer per (person, slot).
 *
 * Drafts are deliberately kept out of the audited person store: they are
 * unreviewed keystrokes, not domain facts. They live in the webview's
 * localStorage so a half-written edit survives an app relaunch (ticket #202),
 * and every surface that edits the same slot (person file, Contacts) reads and
 * writes the same key, so the buffer never forks. Authority is never recorded
 * here — it only flips on an actual Save.
 */

export type DescriptionDraft = {
  text: string;
  /** The saved slot version this draft was started from, for stale detection. */
  baseVersion: number;
};

const KEY_PREFIX = "sourcecado:description-draft:";
const listeners = new Map<string, Set<() => void>>();

function storageKey(personId: string, slot: string): string {
  return `${KEY_PREFIX}${personId}:${slot}`;
}

export function readDescriptionDraft(
  personId: string,
  slot: string,
): DescriptionDraft | null {
  try {
    const raw = window.localStorage.getItem(storageKey(personId, slot));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      typeof (parsed as DescriptionDraft).text === "string" &&
      typeof (parsed as DescriptionDraft).baseVersion === "number"
    ) {
      return {
        text: (parsed as DescriptionDraft).text,
        baseVersion: (parsed as DescriptionDraft).baseVersion,
      };
    }
  } catch {
    // Private windows, blocked storage, or corrupt JSON: behave as no draft.
  }
  return null;
}

export function writeDescriptionDraft(
  personId: string,
  slot: string,
  draft: DescriptionDraft,
): void {
  try {
    window.localStorage.setItem(storageKey(personId, slot), JSON.stringify(draft));
  } catch {
    // Storage may be unavailable; the in-memory editor state still works.
  }
  notify(personId, slot);
}

export function clearDescriptionDraft(personId: string, slot: string): void {
  try {
    window.localStorage.removeItem(storageKey(personId, slot));
  } catch {
    // Ignore: nothing to clear if storage is unavailable.
  }
  notify(personId, slot);
}

function notify(personId: string, slot: string): void {
  const set = listeners.get(storageKey(personId, slot));
  if (!set) return;
  for (const callback of set) callback();
}

/** Subscribe to draft changes for one slot, so a second mounted editor for the
 * same person+slot stays in sync with the first. Returns an unsubscribe. */
export function subscribeDescriptionDraft(
  personId: string,
  slot: string,
  callback: () => void,
): () => void {
  const key = storageKey(personId, slot);
  let set = listeners.get(key);
  if (!set) {
    set = new Set();
    listeners.set(key, set);
  }
  set.add(callback);
  return () => {
    set?.delete(callback);
  };
}
