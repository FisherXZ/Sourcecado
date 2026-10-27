/**
 * Context the director attaches to one message.
 *
 * The backend takes a message as plain text, so a chip travels as a short
 * block of lines after the text. A line holds an id and nothing else: a name
 * from a person file or a file may come from outside, and it must not reach
 * the model as the director's own words. The model reads a person's name from
 * `board_get`, and a file's text from the labelled copy the backend adds when
 * it builds the model request (#253). One helper writes the block and one
 * reads it back, so the thread, the queue and a reloaded conversation all
 * show chips instead of the raw lines.
 */
export type ContextChip = {
  readonly kind: "person";
  readonly id: string;
};

const HEADER = "[Attached by the director]";
const PERSON_LINE = /^Person: person_id (\S+)\. Read the person file with board_get\.$/;
const FILE_LINE = /^File: file_id (att_[0-9a-f]{32})\.$/;

/** The message text with its chips and attached files written after it. */
export function withContext(
  body: string,
  chips: readonly ContextChip[],
  files: readonly string[] = [],
): string {
  if (chips.length === 0 && files.length === 0) return body;
  const lines = [
    ...chips.map((chip) => `Person: person_id ${chip.id}. Read the person file with board_get.`),
    ...files.map((id) => `File: file_id ${id}.`),
  ];
  return `${body.trimEnd()}\n\n${HEADER}\n${lines.join("\n")}`;
}

/**
 * Split a sent message into its text, its chips and its attached file ids.
 *
 * Strict on purpose: if any line after the header is not a chip line or a file
 * line, the whole text stays the message and nothing is shown as attached.
 */
export function splitContext(text: string): {
  readonly body: string;
  readonly chips: readonly ContextChip[];
  readonly files: readonly string[];
} {
  const marker = `\n\n${HEADER}\n`;
  const at = text.lastIndexOf(marker);
  if (at < 0) return { body: text, chips: [], files: [] };
  const chips: ContextChip[] = [];
  const files: string[] = [];
  for (const line of text.slice(at + marker.length).split("\n")) {
    const person = PERSON_LINE.exec(line);
    const file = FILE_LINE.exec(line);
    if (person) chips.push({ kind: "person", id: person[1] });
    else if (file) files.push(file[1]);
    else return { body: text, chips: [], files: [] };
  }
  return { body: text.slice(0, at), chips, files };
}
