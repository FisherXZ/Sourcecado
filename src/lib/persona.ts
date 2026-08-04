import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type { SystemPromptSection } from "./context";

// A persona is the agent's human-authored identity: the static system-prompt
// sections plus the toolset it is allowed to carry. Personas are edited in the
// repo and shipped by PR — the agent reads them and never writes them. Its only
// write path stays add_memory_note (team memory in postgres).
export interface Persona {
  id: string;
  name: string;
  tools: string[];
  sections: SystemPromptSection[];
}

export const DEFAULT_PERSONA_ID = "sourcing";
export const PERSONAS_DIR = "personas";

export class PersonaError extends Error {}

// Reads personas/<id>.md and validates it. Every failure throws: a persona that
// silently loses its doctrine or its tools is a behavior bug with no compile-time
// catch, so a bad edit must fail the request rather than quietly shrink the agent.
export function loadPersona(id: string = DEFAULT_PERSONA_ID, personasDir: string = PERSONAS_DIR): Persona {
  const path = join(resolve(personasDir), `${id}.md`);

  let raw: string;
  try {
    raw = readFileSync(path, "utf8");
  } catch {
    throw new PersonaError(`Persona "${id}" not found at ${path}`);
  }

  const { metadata, body } = splitFrontmatter(raw, id);

  if (metadata.id !== id) {
    throw new PersonaError(`Persona "${id}" declares id "${metadata.id ?? "(missing)"}" — must match its filename`);
  }

  const name = metadata.name?.trim();
  if (!name) {
    throw new PersonaError(`Persona "${id}" is missing a name`);
  }

  const tools = parseToolList(metadata.tools, id);
  const sections = parseSections(body, id);

  return { id, name, tools, sections };
}

function splitFrontmatter(raw: string, id: string): { metadata: Record<string, string>; body: string } {
  const normalized = raw.replace(/\r\n/g, "\n");
  if (!normalized.startsWith("---\n")) {
    throw new PersonaError(`Persona "${id}" is missing its frontmatter block`);
  }

  const closing = normalized.indexOf("\n---\n", 3);
  if (closing === -1) {
    throw new PersonaError(`Persona "${id}" has malformed frontmatter: missing closing fence`);
  }

  const metadata: Record<string, string> = {};
  for (const line of normalized.slice(4, closing).split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const sep = trimmed.indexOf(":");
    if (sep === -1) {
      throw new PersonaError(`Persona "${id}" has a malformed frontmatter line: ${trimmed}`);
    }
    metadata[trimmed.slice(0, sep).trim()] = trimmed.slice(sep + 1).trim();
  }

  return { metadata, body: normalized.slice(closing + 5) };
}

// `tools: [a, b, c]` — a flow list is the only supported shape, matching the
// reference persona format. Duplicates are rejected here rather than at
// registration so the message names the persona.
function parseToolList(value: string | undefined, id: string): string[] {
  if (!value) {
    throw new PersonaError(`Persona "${id}" is missing its tools list`);
  }
  if (!value.startsWith("[") || !value.endsWith("]")) {
    throw new PersonaError(`Persona "${id}" tools must be a list like [a, b] — got: ${value}`);
  }

  const tools = value
    .slice(1, -1)
    .split(",")
    .map((t) => t.trim())
    .filter((t) => t.length > 0);

  if (tools.length === 0) {
    throw new PersonaError(`Persona "${id}" declares an empty tools list`);
  }
  const duplicate = tools.find((t, i) => tools.indexOf(t) !== i);
  if (duplicate) {
    throw new PersonaError(`Persona "${id}" lists tool "${duplicate}" more than once`);
  }

  return tools;
}

// Each `## Heading` block becomes one system-prompt section, in file order —
// the same shape buildSystemPrompt renders back out.
function parseSections(body: string, id: string): SystemPromptSection[] {
  const blocks = body.split(/^## /m).slice(1);

  const sections = blocks.map((block) => {
    const nl = block.indexOf("\n");
    const title = (nl === -1 ? block : block.slice(0, nl)).trim();
    const sectionBody = (nl === -1 ? "" : block.slice(nl + 1)).trim();
    if (!title) {
      throw new PersonaError(`Persona "${id}" has a section with an empty heading`);
    }
    if (!sectionBody) {
      throw new PersonaError(`Persona "${id}" section "${title}" has no body`);
    }
    return { title, body: sectionBody };
  });

  if (sections.length === 0) {
    throw new PersonaError(`Persona "${id}" has no "## " sections`);
  }
  return sections;
}
