/**
 * Context the director attaches to one message.
 *
 * The backend takes a message as plain text, so a chip travels as a short
 * block of lines after the text. One helper writes the block and one reads it
 * back, so the thread, the queue and a reloaded conversation all show the same
 * chips instead of the raw lines.
 */
export type ContextChip = {
  readonly kind: "person";
  readonly id: string;
  readonly name: string;
};

const HEADER = "[Attached by the director]";
const PERSON_NOTE = "Read the person file with board_get.";
const PERSON_LINE = /^Person: (.+) \(person_id: ([^\s)]+)\)\. Read the person file with board_get\.$/;

/** A name from a person file is data: it must not end the block or add lines. */
function lineSafe(value: string): string {
  return value.replace(/[[\]\r\n]+/g, " ").replace(/\s+/g, " ").trim();
}

/** The message text with its chips written after it. */
export function withContext(body: string, chips: readonly ContextChip[]): string {
  if (chips.length === 0) return body;
  const lines = chips.map(
    (chip) => `Person: ${lineSafe(chip.name) || "Unnamed person"} (person_id: ${chip.id}). ${PERSON_NOTE}`,
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
    chips.push({ kind: "person", name: match[1], id: match[2] });
  }
  return { body: text.slice(0, at), chips };
}
