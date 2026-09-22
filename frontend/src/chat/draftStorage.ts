function draftKey(threadId: string): string {
  return `sourcecado.chat.draft.v1:${encodeURIComponent(threadId)}`;
}

export function readDraft(threadId: string): string {
  if (!threadId) return "";
  try {
    return window.localStorage.getItem(draftKey(threadId)) ?? "";
  } catch {
    return "";
  }
}

export function writeDraft(threadId: string, draft: string): void {
  if (!threadId) return;
  try {
    if (draft) window.localStorage.setItem(draftKey(threadId), draft);
    else window.localStorage.removeItem(draftKey(threadId));
  } catch {
    // Draft persistence is best-effort; the live composer remains usable.
  }
}

export function moveDraft(fromThreadId: string, toThreadId: string): boolean {
  const draft = readDraft(fromThreadId);
  if (!draft || !toThreadId) return false;
  try {
    window.localStorage.setItem(draftKey(toThreadId), draft);
    window.localStorage.removeItem(draftKey(fromThreadId));
    return true;
  } catch {
    return false;
  }
}
