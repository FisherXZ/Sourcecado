import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PersonFileView } from "../src/PersonFile";
import { claim, livingBrief } from "./livingBrief";

const api = vi.hoisted(() => ({
  attachPersonMeeting: vi.fn(),
  attachPersonDriveEvidence: vi.fn(),
  getPerson: vi.fn(),
  openPersonSourcingChat: vi.fn(),
  refreshPersonMeetings: vi.fn(),
  rejectPersonMeeting: vi.fn(),
  revertPerson: vi.fn(),
  savePersonHandoff: vi.fn(),
  searchPersonDriveEvidence: vi.fn(),
  setPersonSequence: vi.fn(),
}));

vi.mock("../src/api", () => api);

function personFile(brief = livingBrief()) {
  return {
    person: { person_id: "person one", sequence_state: "open", version: 2, sources: [] },
    brief,
    timeline: [],
    versions: [{ version: 1, created_at: "2026-08-26T10:00:00Z" }],
    sourcing_chat: null,
    meeting_evidence: { attached: [], proposed: [], rejected: [] },
  };
}

describe("the living brief on the person file", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getPerson.mockResolvedValue(personFile());
    api.savePersonHandoff.mockResolvedValue({
      person: { person_id: "person one" },
      brief: livingBrief(),
      saved: true,
      unchanged: false,
    });
  });

  it("separates outcome, last contact, what they want, and knowledge gaps", async () => {
    api.getPerson.mockResolvedValue(
      personFile(
        livingBrief({
          outcome: claim({ id: "outcome:person one", text: "Outcome: agreed to speak" }),
          last_contact: {
            at: "2026-08-25T09:00:00+00:00",
            direction: "inbound",
            replied: true,
            follow_up: { needed: true, reason: "reply_unanswered" },
            claim: null,
          },
          wants: claim({
            id: "wants:person one",
            text: "What they want: a date after the 15th",
            source_refs: ["gmail:event-9"],
          }),
        }),
      ),
    );
    render(<PersonFileView personId="person one" />);

    const outcome = await screen.findByRole("region", { name: "Outcome" });
    expect(within(outcome).getByText("Outcome: agreed to speak")).toBeInTheDocument();
    const contact = screen.getByRole("region", { name: "Last contact" });
    expect(contact).toHaveTextContent("inbound");
    expect(within(contact).getByText("Needs follow-up.")).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "What they want" })).getByText(
        "What they want: a date after the 15th",
      ),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Knowledge gaps" })).getByText(
        "No email address is recorded for this person.",
      ),
    ).toBeInTheDocument();
  });

  it("shows every claim with its state and its source references", async () => {
    api.getPerson.mockResolvedValue(
      personFile(
        livingBrief({
          evidence: [
            claim({
              id: "evidence:event-1",
              text: "Read mail from Alyssa",
              state: "stale",
              source_refs: ["gmail:event-1"],
            }),
            claim({
              id: "notes:event-2",
              text: "Meeting notes (untrusted): pilot in September",
              truncated: true,
              source_refs: ["granola:event-2", "granola:granola-1"],
            }),
          ],
          conflicts: [
            claim({
              id: "conflict:company:event-3",
              text: "company disagrees: the person file says Analytic; web says Difference Engine Co",
              state: "conflicting",
              source_refs: ["sourcecado:person one", "web:event-3"],
            }),
          ],
        }),
      ),
    );
    render(<PersonFileView personId="person one" />);

    const learned = await screen.findByRole("region", { name: "Learned" });
    expect(within(learned).getByText("Stale")).toBeInTheDocument();
    expect(within(learned).getByText("Truncated")).toBeInTheDocument();
    expect(within(learned).getByText("gmail:event-1")).toBeInTheDocument();
    expect(
      within(learned).getByText("granola:event-2 · granola:granola-1"),
    ).toBeInTheDocument();

    const conflicts = screen.getByRole("region", { name: "Conflicts" });
    expect(within(conflicts).getByText("Conflicting")).toBeInTheDocument();
    expect(conflicts).toHaveTextContent("Difference Engine Co");
  });

  it("lists artifacts and source references with their freshness and extraction truth", async () => {
    api.getPerson.mockResolvedValue(
      personFile(
        livingBrief({
          artifacts: [
            claim({
              id: "artifact:artifact_1",
              text: "Artifact: Dinner invitation draft",
              source_refs: ["artifact_1"],
            }),
          ],
          source_refs: [
            {
              id: "source_ref_1",
              provider: "Google Drive",
              locator: "drive-1",
              title: "Fall sourcing masterdoc",
              observed_at: "2026-08-01T10:00:00+00:00",
              modified_at: null,
              fresh: true,
              evidence: "present",
              truncated: false,
            },
            {
              id: "source_ref_2",
              provider: "Google Drive",
              locator: "drive-2",
              title: "Scanned bylaws",
              observed_at: "2026-01-01T10:00:00+00:00",
              modified_at: null,
              fresh: false,
              evidence: "unsupported",
              truncated: false,
            },
          ],
        }),
      ),
    );
    render(<PersonFileView personId="person one" />);

    const artifacts = await screen.findByRole("region", { name: "Artifacts" });
    expect(within(artifacts).getByText("Artifact: Dinner invitation draft")).toBeInTheDocument();

    const sources = screen.getByRole("region", { name: "Source references" });
    expect(within(sources).getByText("Fall sourcing masterdoc")).toBeInTheDocument();
    expect(sources).toHaveTextContent("Read · fresh");
    expect(sources).toHaveTextContent("Body unavailable · stale");
  });

  it("counts a restricted source without naming it", async () => {
    api.getPerson.mockResolvedValue(
      personFile(livingBrief({ restricted_source_count: 1, sources: ["drive"] })),
    );
    render(<PersonFileView personId="person one" />);

    const card = await screen.findByRole("region", { name: "Sources" });
    expect(within(card).getByText("drive")).toBeInTheDocument();
    expect(card).toHaveTextContent("1 restricted source withheld from this brief.");
  });

  it("marks a partial brief rather than showing it as complete", async () => {
    api.getPerson.mockResolvedValue(
      personFile(livingBrief({ partial: true, partial_sources: ["calendar"] })),
    );
    render(<PersonFileView personId="person one" />);

    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent("Partial brief.");
    expect(banner).toHaveTextContent("calendar");
    // The evidence that did land is still on the page.
    const learned = screen.getByRole("region", { name: "Learned" });
    expect(within(learned).getByText("Led university sourcing.")).toBeInTheDocument();
  });

  it("says how many records the brief left out", async () => {
    api.getPerson.mockResolvedValue(personFile(livingBrief({ omitted: 28 })));
    render(<PersonFileView personId="person one" />);

    expect(
      await screen.findByText(/28 older record\(s\) are not in this brief/),
    ).toBeInTheDocument();
  });

  it("lets the director review and version the generated four-field handoff", async () => {
    render(<PersonFileView personId="person one" />);

    const section = await screen.findByRole("region", { name: "Successor handoff" });
    expect(section).toHaveTextContent("Drafted from the claims below.");
    const form = within(section);
    expect(form.getByLabelText("Who this is")).toHaveValue("Alyssa Lee");
    expect(form.getByLabelText("What happened")).toHaveValue("Led university sourcing.");

    fireEvent.change(form.getByLabelText("What happened"), {
      target: { value: "One approved send, no reply yet" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save handoff version" }));

    await waitFor(() =>
      expect(api.savePersonHandoff).toHaveBeenCalledWith("person one", {
        who: "Alyssa Lee",
        wanted: "Strong sourcing fit.",
        happened: "One approved send, no reply yet",
        theyWant: "What this person wants is not recorded.",
        expectedVersion: 2,
      }),
    );
    expect(
      await screen.findByText("Saved as a new person-file version."),
    ).toBeInTheDocument();
  });

  it("shows a saved handoff as saved, at its version", async () => {
    api.getPerson.mockResolvedValue(
      personFile(
        livingBrief({
          handoff: {
            who: "Alyssa Lee, sourcing lead at Codeology",
            wanted: "A research-dinner speaker",
            happened: "One approved send, no reply yet",
            they_want: "A date after the 15th",
            generated: false,
            source_refs: [],
            version: 7,
            saved_at: "2026-08-26T10:00:00+00:00",
            stale: false,
            stale_fields: [],
          },
        }),
      ),
    );
    render(<PersonFileView personId="person one" />);

    const section = await screen.findByRole("region", { name: "Successor handoff" });
    expect(section).toHaveTextContent("Saved at version 7.");
    expect(within(section).getByLabelText("What they want")).toHaveValue(
      "A date after the 15th",
    );
  });

  it("does not invent a version for a legacy saved handoff", async () => {
    api.getPerson.mockResolvedValue(
      personFile(
        livingBrief({
          handoff: {
            who: "Alyssa Lee, sourcing lead at Codeology",
            wanted: "A research-dinner speaker",
            happened: "No reply yet",
            they_want: "Unknown",
            generated: false,
            source_refs: [],
            version: null,
            saved_at: null,
            stale: false,
            stale_fields: [],
            freshness_unknown: true,
          },
        }),
      ),
    );
    render(<PersonFileView personId="person one" />);

    const section = await screen.findByRole("region", { name: "Successor handoff" });
    expect(section).toHaveTextContent(
      "Saved handoff. Its saved version was not recorded; review every field.",
    );
    expect(section).not.toHaveTextContent("Saved at version");
  });

  it("labels stale saved handoff fields without rewriting them", async () => {
    api.getPerson.mockResolvedValue(
      personFile(
        livingBrief({
          handoff: {
            who: "Alyssa Lee, sourcing lead at Codeology",
            wanted: "A research-dinner speaker",
            happened: "No reply yet",
            they_want: "Unknown",
            generated: false,
            source_refs: [],
            version: 7,
            saved_at: "2026-08-26T10:00:00+00:00",
            stale: true,
            stale_fields: ["happened", "they_want"],
          },
        }),
      ),
    );
    render(<PersonFileView personId="person one" />);

    const section = await screen.findByRole("region", { name: "Successor handoff" });
    expect(section).toHaveTextContent(
      "Review outdated fields: What happened and What they want.",
    );
    expect(within(section).getByLabelText("What happened")).toHaveValue("No reply yet");
    expect(within(section).getByLabelText("What they want")).toHaveValue("Unknown");
  });

  it("reports an unchanged handoff without claiming a new version", async () => {
    api.savePersonHandoff.mockResolvedValueOnce({
      person: { person_id: "person one" },
      brief: livingBrief(),
      saved: false,
      unchanged: true,
    });
    render(<PersonFileView personId="person one" />);
    const form = within(
      await screen.findByRole("region", { name: "Successor handoff" }),
    );

    fireEvent.click(form.getByRole("button", { name: "Save handoff version" }));

    expect(await screen.findByText("No handoff changes to save.")).toBeInTheDocument();
    expect(
      screen.queryByText("Saved as a new person-file version."),
    ).not.toBeInTheDocument();
  });

  it("keeps a failed handoff save visible without losing the edit", async () => {
    api.savePersonHandoff.mockRejectedValueOnce(new Error("stale person version"));
    render(<PersonFileView personId="person one" />);
    const form = within(
      await screen.findByRole("region", { name: "Successor handoff" }),
    );

    fireEvent.change(form.getByLabelText("What they want"), {
      target: { value: "A date after the 15th" },
    });
    fireEvent.click(form.getByRole("button", { name: "Save handoff version" }));

    expect(
      await screen.findByText(/Couldn’t save the handoff/),
    ).toBeInTheDocument();
    expect(form.getByLabelText("What they want")).toHaveValue("A date after the 15th");
  });
});
