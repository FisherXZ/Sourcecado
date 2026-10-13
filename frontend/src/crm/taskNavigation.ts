// One active editor in the app. Hash routing asks before unmounting its draft.
let guard: ((destination: string) => boolean) | null = null;

export function allowTaskNavigation(destination: string): boolean {
  return guard?.(destination) ?? true;
}
export function registerTaskNavigation(callback: (destination: string) => boolean) {
  guard = callback;
  return () => { if (guard === callback) guard = null; };
}
