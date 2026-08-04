import { mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { loadPersona, PersonaError, DEFAULT_PERSONA_ID } from "@/lib/persona";
import { memoryRegistry } from "@/lib/memory/answer-config";

const dirs: string[] = [];

function personaDirWith(contents: string, id = "probe"): string {
  const dir = mkdtempSync(join(tmpdir(), "persona-"));
  dirs.push(dir);
  writeFileSync(join(dir, `${id}.md`), contents, "utf8");
  return dir;
}

afterEach(() => {
  while (dirs.length) rmSync(dirs.pop()!, { recursive: true, force: true });
});

describe("the shipped sourcing persona", () => {
  it("loads with its id, name, tools, and seven sections", () => {
    const persona = loadPersona();
    expect(persona.id).toBe(DEFAULT_PERSONA_ID);
    expect(persona.name).toBe("Sourcecado Sourcing Agent");
    expect(persona.tools).toEqual([
      "search_memory",
      "add_memory_note",
      "web_search",
      "web_fetch",
      "apollo_search_people",
      "apollo_enrich_contact",
    ]);
    expect(persona.sections).toHaveLength(7);
  });

  // The prose was approved verbatim by Fisher on 2026-07-15. Before J1 the only
  // guard was review attention, and a real drift slipped through. This asserts the
  // shipped persona against the approved doc so drift fails CI instead of review.
  it("matches the approved v5 spec verbatim, section for section", () => {
    const spec = readFileSync(
      "docs/superpowers/plans/2026-07-15-sourcing-agent-system-prompt.md",
      "utf8"
    );
    const approved = spec
      .split("## THE PROMPT")[1]
      .split(/^---$/m)[0]
      .split(/^### /m)
      .slice(1)
      .map((block) => {
        const nl = block.indexOf("\n");
        return {
          title: block.slice(0, nl).trim().replace(/^\d+\s*·\s*/, ""),
          body: block.slice(nl + 1).trim(),
        };
      });

    const norm = (s: string) => s.replace(/\s+/g, " ").trim();
    const shipped = loadPersona().sections;

    expect(shipped).toHaveLength(approved.length);
    approved.forEach((section, i) => {
      expect(norm(shipped[i].body), `§${i + 1} body drifted from the approved spec`).toBe(
        norm(section.body)
      );
    });
  });

  it("declares only tools the chat path implements", () => {
    const registry = memoryRegistry(loadPersona());
    const names = registry
      .list(new Set(["read", "enrich", "reason", "draft", "write_internal", "admin"] as const))
      .map((t) => t.name)
      .sort();
    expect(names).toEqual(loadPersona().tools.slice().sort());
  });

  // The boundary Fisher set: a human authors the persona, the agent writes only to
  // memory. If a future tool lets the agent write files, this catches it before the
  // agent can edit its own identity.
  it("gives the agent exactly one write path, and it is memory", () => {
    const writers = memoryRegistry(loadPersona())
      .list(new Set(["write_internal", "admin"] as const))
      .map((t) => t.name);
    expect(writers).toEqual(["add_memory_note"]);
  });
});

describe("persona validation", () => {
  const body = "## Identity & mission\n\nYou are a probe.\n";
  const frontmatter = "---\nid: probe\nname: Probe\ntools: [search_memory]\n---\n\n";

  it("accepts a well-formed persona", () => {
    const persona = loadPersona("probe", personaDirWith(frontmatter + body));
    expect(persona.tools).toEqual(["search_memory"]);
    expect(persona.sections).toEqual([{ title: "Identity & mission", body: "You are a probe." }]);
  });

  it("throws when the persona file is missing", () => {
    expect(() => loadPersona("nope", personaDirWith(frontmatter + body))).toThrow(PersonaError);
  });

  it("throws when frontmatter is absent", () => {
    expect(() => loadPersona("probe", personaDirWith(body))).toThrow(/missing its frontmatter/);
  });

  it("throws when the closing fence is missing", () => {
    expect(() => loadPersona("probe", personaDirWith("---\nid: probe\n" + body))).toThrow(
      /missing closing fence/
    );
  });

  it("throws when the declared id disagrees with the filename", () => {
    const dir = personaDirWith("---\nid: other\nname: Probe\ntools: [search_memory]\n---\n\n" + body);
    expect(() => loadPersona("probe", dir)).toThrow(/must match its filename/);
  });

  it("throws when the name is missing", () => {
    const dir = personaDirWith("---\nid: probe\ntools: [search_memory]\n---\n\n" + body);
    expect(() => loadPersona("probe", dir)).toThrow(/missing a name/);
  });

  it("throws when tools are missing, empty, or not a list", () => {
    expect(() => loadPersona("probe", personaDirWith("---\nid: probe\nname: P\n---\n\n" + body))).toThrow(
      /missing its tools list/
    );
    expect(() =>
      loadPersona("probe", personaDirWith("---\nid: probe\nname: P\ntools: []\n---\n\n" + body))
    ).toThrow(/empty tools list/);
    expect(() =>
      loadPersona("probe", personaDirWith("---\nid: probe\nname: P\ntools: search_memory\n---\n\n" + body))
    ).toThrow(/must be a list/);
  });

  it("throws on a duplicated tool", () => {
    const dir = personaDirWith(
      "---\nid: probe\nname: P\ntools: [search_memory, search_memory]\n---\n\n" + body
    );
    expect(() => loadPersona("probe", dir)).toThrow(/more than once/);
  });

  it("throws when a section has no body", () => {
    expect(() => loadPersona("probe", personaDirWith(frontmatter + "## Identity & mission\n"))).toThrow(
      /has no body/
    );
  });

  it("throws when the persona has no sections", () => {
    expect(() => loadPersona("probe", personaDirWith(frontmatter + "just prose\n"))).toThrow(
      /no "## " sections/
    );
  });
});

describe("memoryRegistry driven by a persona", () => {
  it("registers exactly the persona's declared tools", () => {
    const dir = personaDirWith(
      "---\nid: probe\nname: P\ntools: [search_memory, web_search]\n---\n\n## Identity & mission\n\nProbe.\n"
    );
    const registry = memoryRegistry(loadPersona("probe", dir));
    expect(registry.get("search_memory")).toBeDefined();
    expect(registry.get("web_search")).toBeDefined();
    expect(registry.get("add_memory_note")).toBeUndefined();
  });

  it("throws on an unknown tool instead of silently shrinking the agent", () => {
    const dir = personaDirWith(
      "---\nid: probe\nname: P\ntools: [search_memory, telepathy]\n---\n\n## Identity & mission\n\nProbe.\n"
    );
    expect(() => memoryRegistry(loadPersona("probe", dir))).toThrow(/unknown tool "telepathy"/);
  });
});
