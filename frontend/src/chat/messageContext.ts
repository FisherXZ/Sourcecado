/**
 * Context the director attaches to one message.
 *
 * The backend takes a message as plain text, so a chip travels as a short
 * block of lines after the text. A line holds an id and nothing else: a name
 * from a person file may come from outside, and it must not reach the model
 * as the director's own words. The model reads the name from `board_get`.
 * One helper writes the block and one reads it back, so the thread, the queue
 * and a reloaded conversation all show chips instead of the raw lines.
 */
export type ContextChip = {
  readonly kind: "person";
  readonly id: string;
};

const HEADER = "[Attached by the director]";
const PERSON_LINE = /^Person: person_id (\S+)\. Read the person file with board_get\.$/;

/** The message text with its chips written after it. */
export function withContext(body: string, chips: readonly ContextChip[]): string {
  if (chips.length === 0) return body;
  const lines = chips.map(
    (chip) => `Person: person_id ${chip.id}. Read the person file with board_get.`,
  );
  return `${body.trimEnd()}\n\n${HEADER}\n${lines.join("\n")}`;
}

/**
 * Split a sent message into its text and its chips.
 *
 * Strict on purpose: if any line after the header is not a chip line, the
 * whole text stays the message and no chip is shown.
 */
export function splitContext(text: string): {
  readonly body: string;
  readonly chips: readonly ContextChip[];
} {
  const marker = `\n\n${HEADER}\n`;
  const at = text.lastIndexOf(marker);
  if (at < 0) return { body: text, chips: [] };
  const chips: ContextChip[] = [];
  for (const line of text.slice(at + marker.length).split("\n")) {
    const match = PERSON_LINE.exec(line);
    if (!match) return { body: text, chips: [] };
    chips.push({ kind: "person", id: match[1] });
  }
  return { body: text.slice(0, at), chips };
}
