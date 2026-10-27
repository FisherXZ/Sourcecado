import { afterAll, beforeAll, describe, expect, it } from "vitest";

import type { Board, BoardPerson, SkillInfo } from "../src/api";
import { buildPrompts, contactTime, greetingFor } from "../src/chat/emptyPrompts";
import { connector } from "./connectorFixtures";

function person(overrides: Partial<BoardPerson> & Pick<BoardPerson, "person_id">): BoardPerson {
  return {
    first_name: null,
    last_name: null,
    title: null,
    company: null,
    sequence_state: "open",
    ...overrides,
  };
}

function board(overrides: Partial<Board> = {}): Board {
  return { backlog: [], open: [], in_conversation: [], done: [], ...overrides };
}

const skill = (name: string, source: SkillInfo["source"]): SkillInfo => ({
  name,
  purpose: "",
  useWhen: "",
  source,
  status: "ready",
  instructions: "",
});

describe("greetingFor", () => {
  it("reads the local hour and never adds a name", () => {
    expect(greetingFor(new Date(2026, 9, 26, 8))).toBe("Good morning");
    expect(greetingFor(new Date(2026, 9, 26, 12))).toBe("Good afternoon");
    expect(greetingFor(new Date(2026, 9, 26, 16, 59))).toBe("Good afternoon");
    expect(greetingFor(new Date(2026, 9, 26, 17))).toBe("Good evening");
    expect(greetingFor(new Date(2026, 9, 26, 3))).toBe("Good evening");
  });
});

describe("buildPrompts", () => {
  it("returns no chip when no source answered", () => {
    expect(buildPrompts({ board: null, connectors: null, skills: null })).toEqual([]);
  });

  it("returns no chip for empty sources", () => {
    expect(buildPrompts({ board: board(), connectors: [], skills: [] })).toEqual([]);
  });

  it("names the company of the most recently contacted active person", () => {
    const chips = buildPrompts({
      board: board({
        open: [
          person({ person_id: "a", company: "Older Co", last_contact_at: "2026-10-01T10:00:00Z" }),
          person({ person_id: "b", company: "Newest Co", last_contact_at: "2026-10-20T10:00:00Z" }),
        ],
        in_conversation: [
          person({ person_id: "c", company: "Middle Co", last_contact_at: "2026-10-10T10:00:00Z" }),
        ],
        // A done person and a backlog person are not an active target.
        done: [person({ person_id: "d", company: "Done Co", last_contact_at: "2026-10-25T10:00:00Z" })],
        backlog: [person({ person_id: "e", company: "Backlog Co", last_contact_at: "2026-10-26T10:00:00Z" })],
      }),
      connectors: null,
      skills: null,
    });
    expect(chips).toHaveLength(1);
    expect(chips[0]).toMatchObject({ id: "target", label: "Catch up on Newest Co" });
  });

  it("names the director's target for the most recently contacted active person, and falls back to the company", () => {
    const withTarget = buildPrompts({
      board: board({
        open: [
          person({
            person_id: "a",
            company: "Acme Robotics",
            target: "Seed-stage AI infra founders in the Bay Area",
            last_contact_at: "2026-10-20T10:00:00Z",
          }),
          person({
            person_id: "b",
            company: "Older Co",
            target: "Older target",
            last_contact_at: "2026-10-01T10:00:00Z",
          }),
        ],
      }),
      connectors: null,
      skills: null,
    });
    expect(withTarget[0].label).toBe("Catch up on Seed-stage AI infra founders in the Bay Area");
    expect(withTarget[0].prompt).toContain("Seed-stage AI infra founders in the Bay Area");

    // An empty or blank target is not a target.
    for (const target of [null, "", "   "]) {
      const [chip] = buildPrompts({
        board: board({
          open: [
            person({
              person_id: "a",
              company: "Acme Robotics",
              target,
              last_contact_at: "2026-10-20T10:00:00Z",
            }),
          ],
        }),
        connectors: null,
        skills: null,
      });
      expect(chip.label).toBe("Catch up on Acme Robotics");
    }
  });

  it("falls back to the person's name when there is no company, and skips people never contacted", () => {
    const chips = buildPrompts({
      board: board({
        open: [
          person({ person_id: "a", first_name: "Never", last_name: "Contacted" }),
          person({
            person_id: "b",
            first_name: "Maya",
            last_name: "Chen",
            last_contact_at: "2026-10-20T10:00:00Z",
          }),
        ],
      }),
      connectors: null,
      skills: null,
    });
    expect(chips.map((chip) => chip.label)).toEqual(["Catch up on Maya Chen"]);
    expect(
      buildPrompts({
        board: board({ open: [person({ person_id: "a", company: "Acme" })] }),
        connectors: null,
        skills: null,
      }),
    ).toEqual([]);
  });

  it("counts follow-ups in the lanes the Contacts page shows", () => {
    const needs = { needed: true, reason: "reply_unanswered" as const };
    const chips = buildPrompts({
      board: board({
        open: [person({ person_id: "a", follow_up: needs })],
        in_conversation: [person({ person_id: "b", follow_up: needs })],
        done: [person({ person_id: "c", follow_up: needs })],
        backlog: [person({ person_id: "d", follow_up: needs })],
      }),
      connectors: null,
      skills: null,
    });
    expect(chips).toHaveLength(1);
    expect(chips[0]).toMatchObject({ id: "follow-up", label: "Follow up with 3 people" });
    expect(chips[0].prompt).toContain("3 contacts");
  });

  it("names a single follow-up and never shows a hidden surname", () => {
    const needs = { needed: true, reason: "reply_needs_review" as const };
    const [chip] = buildPrompts({
      board: board({
        open: [
          person({
            person_id: "a",
            first_name: "Maya",
            last_name: "C***",
            last_name_status: "hidden_by_apollo",
            follow_up: needs,
          }),
        ],
      }),
      connectors: null,
      skills: null,
    });
    expect(chip.label).toBe("Follow up with Maya");
    expect(`${chip.label} ${chip.prompt}`).not.toContain("*");
  });

  it("picks the first connector that needs attention, not one that is only not connected", () => {
    const [chip] = buildPrompts({
      board: null,
      connectors: [
        connector({ id: "gmail", title: "Gmail" }),
        connector({ id: "drive", title: "Drive", status: "reconnect_required" }),
        connector({ id: "calendar", title: "Calendar", status: "failed" }),
      ],
      skills: null,
    });
    expect(chip).toMatchObject({ id: "connector", label: "Fix Drive" });
    expect(chip.prompt).toContain("Reconnect required");
    expect(
      buildPrompts({
        board: null,
        connectors: [connector({ id: "gmail", title: "Gmail" })],
        skills: null,
      }),
    ).toEqual([]);
  });

  it("prefers a workspace skill over a built-in one", () => {
    const skills = [skill("weekly-sourcing", "builtin"), skill("board-prep", "workspace")];
    expect(buildPrompts({ board: null, connectors: null, skills })[0]).toMatchObject({
      id: "skill",
      label: "Run Board prep",
      prompt: "Use the board-prep skill.",
    });
    expect(buildPrompts({ board: null, connectors: null, skills: skills.slice(0, 1) })[0].label).toBe(
      "Run Weekly sourcing",
    );
  });

  it("words one nameless follow-up in the singular", () => {
    const [chip] = buildPrompts({
      board: board({
        open: [
          person({
            person_id: "a",
            last_name: "C***",
            last_name_status: "hidden_by_apollo",
            follow_up: { needed: true, reason: "reply_unanswered" },
          }),
        ],
      }),
      connectors: null,
      skills: null,
    });
    expect(chip.label).toBe("Follow up with 1 person");
    expect(chip.prompt).toBe(
      "Review the contact that needs follow-up and propose the next step.",
    );
  });

  it("drops only the board chips for a board with no lanes, and keeps the others", () => {
    const connectors = [connector({ id: "gmail", title: "Gmail", status: "degraded" })];
    const skills = [skill("weekly-sourcing", "builtin")];
    for (const malformed of [{}, { open: null, in_conversation: "x", done: 3 }]) {
      const chips = buildPrompts({ board: malformed as unknown as Board, connectors, skills });
      expect(chips.map((chip) => chip.id)).toEqual(["connector", "skill"]);
    }
  });

  it("keeps a good lane when another lane is missing", () => {
    const board = {
      in_conversation: [
        person({
          person_id: "a",
          company: "Acme",
          last_contact_at: "2026-10-20T10:00:00Z",
          follow_up: { needed: true, reason: "reply_unanswered" },
        }),
      ],
    } as unknown as Board;
    expect(buildPrompts({ board, connectors: null, skills: null }).map((chip) => chip.id)).toEqual([
      "target",
      "follow-up",
    ]);
  });

  it("drops the board chips, not the others, when a person row is not an object", () => {
    const chips = buildPrompts({
      board: board({ open: [null as unknown as BoardPerson] }),
      connectors: null,
      skills: [skill("weekly-sourcing", "builtin")],
    });
    expect(chips.map((chip) => chip.id)).toEqual(["skill"]);
  });

  it("keeps the order target, follow-up, connector, skill", () => {
    const chips = buildPrompts({
      board: board({
        open: [
          person({
            person_id: "a",
            company: "Acme",
            last_contact_at: "2026-10-20T10:00:00Z",
            follow_up: { needed: true, reason: "reply_unanswered" },
          }),
        ],
      }),
      connectors: [connector({ id: "gmail", title: "Gmail", status: "degraded" })],
      skills: [skill("weekly-sourcing", "builtin")],
    });
    expect(chips.map((chip) => chip.id)).toEqual(["target", "follow-up", "connector", "skill"]);
  });
});

describe("contactTime under a zone that is not UTC", () => {
  const previous = process.env.TZ;
  beforeAll(() => {
    process.env.TZ = "America/Los_Angeles";
  });
  afterAll(() => {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  });

  it("really runs in a zone that is not UTC", () => {
    // Without this, the tests below would pass on a machine set to UTC.
    expect(new Date(2026, 9, 27, 12).getTimezoneOffset()).not.toBe(0);
  });

  it("reads a stamp with no zone as UTC", () => {
    expect(contactTime("2026-10-27 02:48:49")).toBe(Date.UTC(2026, 9, 27, 2, 48, 49));
    expect(contactTime("2026-10-27T02:48:49")).toBe(Date.UTC(2026, 9, 27, 2, 48, 49));
    expect(contactTime("2026-10-27 02:48:49.250")).toBe(Date.UTC(2026, 9, 27, 2, 48, 49, 250));
  });

  it("leaves a stamp that has a zone alone, and returns NaN for text that is not a time", () => {
    expect(contactTime("2026-10-27T05:48:49Z")).toBe(Date.UTC(2026, 9, 27, 5, 48, 49));
    expect(contactTime("2026-10-27T05:48:49+02:00")).toBe(Date.UTC(2026, 9, 27, 3, 48, 49));
    expect(Number.isNaN(contactTime("not a time"))).toBe(true);
    expect(Number.isNaN(contactTime(null))).toBe(true);
    expect(Number.isNaN(contactTime(undefined))).toBe(true);
  });

  it("puts a send and a later reply in the order they happened", () => {
    // Alpha: sent 02:48:49 UTC. Bravo: reply 05:48:49 UTC, three hours later.
    // Read as local time in Pacific, the send would land 7 hours late and win.
    const [chip] = buildPrompts({
      board: board({
        open: [
          person({
            person_id: "alpha",
            company: "Alpha Co",
            last_contact_at: "2026-10-27 02:48:49",
          }),
        ],
        in_conversation: [
          person({
            person_id: "bravo",
            company: "Bravo Co",
            last_contact_at: "2026-10-27T05:48:49Z",
          }),
        ],
      }),
      connectors: null,
      skills: null,
    });
    expect(chip.label).toBe("Catch up on Bravo Co");
  });
});
