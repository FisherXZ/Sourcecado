import { useEffect, useSyncExternalStore } from "react";

import { getBoard, type Board, type BoardPerson } from "../api";
import { withoutApolloNameMasks } from "../personName";

/**
 * Names for the person chips.
 *
 * A chip travels as an id only. Its label is read from the Board by that id,
 * every time it is drawn, so text that came from outside (an Apollo name, for
 * example) is never copied into the director's message.
 */
export const UNKNOWN_PERSON = "Unknown person";
const MAX_LABEL = 80;
const RETRY_MS = 5000;

type Snapshot = {
  readonly names: ReadonlyMap<string, string>;
  /** A Board read has finished, whether or not it worked. */
  readonly settled: boolean;
};

let snapshot: Snapshot = { names: new Map(), settled: false };
let loading = false;
let lastAttempt = 0;
const listeners = new Set<() => void>();

function publish(next: Snapshot) {
  snapshot = next;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function boardPersonName(person: BoardPerson): string {
  const name = withoutApolloNameMasks(
    [person.first_name, person.last_name].filter(Boolean).join(" "),
  );
  return name || UNKNOWN_PERSON;
}

/** Take the names from a Board read. A person who is gone from it is gone here. */
export function rememberBoard(board: Board): void {
  const names = new Map<string, string>();
  for (const lane of [board.open, board.in_conversation, board.done, board.backlog]) {
    for (const person of lane) names.set(person.person_id, boardPersonName(person));
  }
  publish({ names, settled: true });
}

async function readBoard(): Promise<void> {
  rememberBoard(await getBoard());
}

function refresh(): void {
  if (loading) return;
  loading = true;
  lastAttempt = Date.now();
  readBoard()
    .catch(() => publish({ names: snapshot.names, settled: true }))
    .finally(() => {
      loading = false;
    });
}

if (typeof window !== "undefined") {
  window.addEventListener("sourcecado:board-changed", () => {
    if (listeners.size > 0) refresh();
  });
}

/** Clear what was read. For tests. */
export function resetPersonNames(): void {
  snapshot = { names: new Map(), settled: false };
  loading = false;
  lastAttempt = 0;
}

/** A name cut to a length that fits a chip or a menu row. */
export function shortLabel(name: string): string {
  return name.length > MAX_LABEL ? `${name.slice(0, MAX_LABEL)}…` : name;
}

/** The current Board name for a person id, shortened to fit a chip. */
export function usePersonName(id: string): string {
  const { names, settled } = useSyncExternalStore(subscribe, () => snapshot);
  const known = names.get(id);
  useEffect(() => {
    if (!known && Date.now() - lastAttempt > RETRY_MS) refresh();
  }, [known, settled]);
  return shortLabel(known ?? (settled ? UNKNOWN_PERSON : "Person"));
}
