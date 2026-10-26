/**
 * Unsent follow-up text, saved per person and task in localStorage.
 *
 * Keeps the director's text when Gmail can't take it yet (no email address,
 * Gmail not connected, or a thread problem). Nothing here can be sent;
 * sending always needs a Gmail draft and an approval.
 */

export type FollowUpText = {
  subject: string;
  body: string;
  /** The thread the text is for, or null for a new email. */
  threadId: string | null;
  /** The Gmail draft holding this text, if there is one. */
  draftId: string | null;
};

const KEY_PREFIX = "sourcecado:follow-up-draft:";

function storageKey(personId: string, taskId: string): string {
  return `${KEY_PREFIX}${personId}:${taskId}`;
}

export function readFollowUpText(personId: string, taskId: string): FollowUpText | null {
  try {
    const raw = window.localStorage.getItem(storageKey(personId, taskId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<FollowUpText>;
    if (typeof parsed.subject === "string" && typeof parsed.body === "string") {
      return {
        subject: parsed.subject,
        body: parsed.body,
        threadId: typeof parsed.threadId === "string" ? parsed.threadId : null,
        draftId: typeof parsed.draftId === "string" ? parsed.draftId : null,
      };
    }
  } catch {
    // Storage blocked or data unreadable: treat as no saved text.
  }
  return null;
}

export function writeFollowUpText(personId: string, taskId: string, text: FollowUpText): void {
  try {
    window.localStorage.setItem(storageKey(personId, taskId), JSON.stringify(text));
  } catch {
    // Storage may be blocked; the editor still works.
  }
}

export function clearFollowUpText(personId: string, taskId: string): void {
  try {
    window.localStorage.removeItem(storageKey(personId, taskId));
  } catch {
    // Nothing to clear if storage is blocked.
  }
}
