import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ToolCallMessagePart } from "@assistant-ui/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ActivityGroup } from "../src/chat/ActivityGroup";
import { Inspector, InspectorProvider } from "../src/chat/Inspector";

const api = vi.hoisted(() => ({
  curateApolloCandidates: vi.fn(),
  openPersonSourcingChat: vi.fn(),
}));

vi.mock("../src/api", () => ({
  curateApolloCandidates: api.curateApolloCandidates,
  openPersonSourcingChat: api.openPersonSourcingChat,
}));

function apolloSearch(
  overrides: Partial<ToolCallMessagePart> = {},
): ToolCallMessagePart {
  return {
    type: "tool-call",
    toolCallId: "apollo-search-1",
    toolName: "apollo_search_people",
    args: {
      organizationName: "Apollo",
      personTitles: ["CEO"],
    },
    argsText: "{}",
    result: {
      people: [
        {
          apolloId: "person-tim",
          firstName: "Tim",
          lastNameObfuscated: "Zh***g",
          title: "CEO",
          organizationName: "Apollo.io",
          hasEmail: true,
          directPhoneStatus: "Yes",
        },
        {
          apolloId: "person-partial",
          firstName: "Maya",
          lastNameObfuscated: null,
          title: null,
          organizationName: "Apollo.io",
          hasEmail: false,
          directPhoneStatus: null,
        },
      ],
    },
    ...overrides,
  };
}

function apolloEnrich(
  overrides: Partial<ToolCallMessagePart> = {},
): ToolCallMessagePart {
  return {
    type: "tool-call",
    toolCallId: "apollo-enrich-1",
    toolName: "apollo_enrich_contact",
    args: {
      firstName: "Tim",
      lastName: "Zheng",
      organizationName: "Apollo",
    },
    argsText: "{}",
    result: {
      name: "Tim Zheng",
      title: "CEO",
      organizationName: "Apollo.io",
      linkedinUrl: "https://www.linkedin.com/in/timzheng",
      email: "tim@apollo.io",
      phone: null,
    },
    ...overrides,
  };
}

function renderApollo(tool = apolloSearch()) {
  return render(
    <InspectorProvider threadId="thread-apollo">
      <ActivityGroup tools={[tool]} messageState="complete" />
      <Inspector />
    </InspectorProvider>,
  );
}

describe("Apollo people result", () => {
  beforeEach(() => {
    api.curateApolloCandidates.mockReset();
    api.curateApolloCandidates.mockResolvedValue({
      status: "success",
      selected_row_count: 2,
      selected_identity_count: 2,
      kept: [],
      failed: [],
      duplicates: [],
      original_session: {
        session_id: "thread-apollo",
        bound_person_id: null,
        reason: "multiple_selection",
      },
    });
    api.openPersonSourcingChat.mockReset();
  });

  it("shows an honest incomplete name without rendering Apollo's mask", () => {
    const { container } = renderApollo();
    fireEvent.click(
      screen.getByRole("button", { name: "Searched Apollo · Completed" }),
    );

    expect(screen.getByText("Tim")).toBeInTheDocument();
    expect(screen.getByText("Surname hidden by Apollo")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Select Tim" })).toBeInTheDocument();
    expect(container).not.toHaveTextContent("Zh***g");
  });

  it("lets the director select several candidates and review before keeping", async () => {
    renderApollo();
    fireEvent.click(
      screen.getByRole("button", { name: "Searched Apollo · Completed" }),
    );

    fireEvent.click(screen.getByRole("checkbox", { name: "Select Tim" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Maya" }));
    fireEvent.change(
      screen.getByRole("textbox", { name: "Target for selected people" }),
      { target: { value: "Invite senior operators to the director's dinner." } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Review 2 selected candidates" }),
    );

    const review = screen.getByRole("region", {
      name: "Review selected Apollo candidates",
    });
    expect(review).toHaveTextContent("Tim");
    expect(review).not.toHaveTextContent("Zh***g");
    expect(review).toHaveTextContent("Maya");
    expect(review).toHaveTextContent(
      "Invite senior operators to the director's dinner.",
    );
    expect(review).toHaveTextContent("does not enrich or use Apollo credits");
    expect(api.curateApolloCandidates).not.toHaveBeenCalled();

    fireEvent.click(within(review).getByRole("button", { name: "Keep 2 people" }));
    await waitFor(() =>
      expect(api.curateApolloCandidates).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: "thread-apollo",
          target: "Invite senior operators to the director's dinner.",
          bindOriginal: false,
        }),
      ),
    );
    expect(api.curateApolloCandidates.mock.calls[0]?.[0].people).toHaveLength(2);
  });

  it("binds the original conversation only for one reviewed candidate", async () => {
    api.curateApolloCandidates.mockResolvedValueOnce({
      status: "success",
      selected_row_count: 1,
      selected_identity_count: 1,
      kept: [
        {
          row_index: 0,
          apollo_id: "person-tim",
          person_id: "person-tim-file",
          version: 1,
          operation: "created",
          first_name: "Tim",
          last_name: null,
          last_name_status: "hidden_by_apollo",
          title: "CEO",
          company: "Apollo.io",
          sourcing_chat: { session_id: "thread-apollo" },
        },
      ],
      failed: [],
      duplicates: [],
      original_session: {
        session_id: "thread-apollo",
        bound_person_id: "person-tim-file",
        reason: "single_selection",
      },
    });
    renderApollo();
    fireEvent.click(screen.getByRole("button", { name: "Searched Apollo · Completed" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Tim" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Target for selected people" }), {
      target: { value: "Director-authored target" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review 1 selected candidate" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep 1 person" }));

    await waitFor(() =>
      expect(api.curateApolloCandidates).toHaveBeenCalledWith(
        expect.objectContaining({ bindOriginal: true }),
      ),
    );
    expect(
      await screen.findByRole("button", {
        name: "Open sourcing chat for Tim (surname hidden by Apollo)",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/original target conversation remains unbound/i)).not.toBeInTheDocument();
  });

  it("deduplicates repeated Apollo identities before selection", () => {
    renderApollo(
      apolloSearch({
        result: {
          people: [
            (apolloSearch().result as { people: unknown[] }).people[0],
            {
              ...(apolloSearch().result as { people: Record<string, unknown>[] }).people[0],
              title: "Duplicate must not replace first",
            },
          ],
        },
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Searched Apollo · Completed" }));

    expect(screen.getAllByRole("checkbox", { name: "Select Tim" })).toHaveLength(1);
    expect(screen.getByText("1 candidate")).toBeInTheDocument();
    expect(screen.queryByText("Duplicate must not replace first")).not.toBeInTheDocument();
  });

  it("offers a direct person file and create-chat action for every kept row", async () => {
    api.curateApolloCandidates.mockResolvedValueOnce({
      status: "success",
      selected_row_count: 2,
      selected_identity_count: 2,
      kept: [
        {
          row_index: 0,
          apollo_id: "person-tim",
          person_id: "person-tim-file",
          version: 1,
          operation: "created",
          first_name: "Tim",
          last_name: null,
          last_name_status: "hidden_by_apollo",
          title: "CEO",
          company: "Apollo.io",
          sourcing_chat: null,
        },
        {
          row_index: 1,
          apollo_id: "person-partial",
          person_id: "person-maya-file",
          version: 1,
          operation: "created",
          first_name: "Maya",
          last_name: null,
          last_name_status: "missing",
          title: null,
          company: "Apollo.io",
          sourcing_chat: null,
        },
      ],
      failed: [],
      duplicates: [],
      original_session: {
        session_id: "thread-apollo",
        bound_person_id: null,
        reason: "multiple_selection",
      },
    });
    api.openPersonSourcingChat.mockResolvedValueOnce({
      created: true,
      session: { id: "thread-tim", title: "Sourcing · Tim", n_msgs: 0 },
      active_person: { person_id: "person-tim-file", version: 1, label: "Tim" },
    });
    renderApollo();
    fireEvent.click(screen.getByRole("button", { name: "Searched Apollo · Completed" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Tim" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Maya" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Target for selected people" }), {
      target: { value: "Director-authored target" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review 2 selected candidates" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep 2 people" }));

    expect(
      await screen.findByRole("link", {
        name: "Open person file for Tim (surname hidden by Apollo)",
      }),
    ).toHaveAttribute("href", "#/people/person-tim-file");
    expect(
      screen.getByRole("link", { name: "Open person file for Maya" }),
    ).toHaveAttribute("href", "#/people/person-maya-file");
    fireEvent.click(
      screen.getByRole("button", {
        name: "Create sourcing chat for Tim (surname hidden by Apollo)",
      }),
    );
    await waitFor(() =>
      expect(api.openPersonSourcingChat).toHaveBeenCalledWith("person-tim-file", 1),
    );
    expect(window.location.hash).toBe(
      "#/chat/thread-tim/person/person-tim-file",
    );
  });

  it("shows a visible error when a kept person's chat cannot be opened", async () => {
    api.curateApolloCandidates.mockResolvedValueOnce({
      status: "success",
      selected_row_count: 1,
      selected_identity_count: 1,
      kept: [
        {
          row_index: 0,
          apollo_id: "person-tim",
          person_id: "person-tim-file",
          version: 1,
          operation: "created",
          first_name: "Tim",
          last_name: null,
          last_name_status: "hidden_by_apollo",
          title: "CEO",
          company: "Apollo.io",
          sourcing_chat: null,
        },
      ],
      failed: [],
      duplicates: [],
      original_session: {
        session_id: "thread-apollo",
        bound_person_id: null,
        reason: "existing_person_chat",
      },
    });
    api.openPersonSourcingChat.mockRejectedValueOnce(new Error("stale person version"));
    renderApollo();
    fireEvent.click(screen.getByRole("button", { name: "Searched Apollo · Completed" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Tim" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Target for selected people" }), {
      target: { value: "Director-authored target" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review 1 selected candidate" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep 1 person" }));
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Create sourcing chat for Tim (surname hidden by Apollo)",
      }),
    );

    expect(
      await screen.findByRole("alert", { name: "Sourcing chat unavailable" }),
    ).toHaveTextContent("Couldn’t open this person’s sourcing chat");
  });

  it("retries only failed rows while preserving successful receipts", async () => {
    api.curateApolloCandidates
      .mockResolvedValueOnce({
        status: "partial",
        selected_row_count: 2,
        selected_identity_count: 2,
        kept: [
          {
            row_index: 0,
            apollo_id: "person-tim",
            person_id: "person-tim-file",
            version: 1,
            operation: "created",
            first_name: "Tim",
            last_name: null,
            last_name_status: "hidden_by_apollo",
            title: "CEO",
            company: "Apollo.io",
            sourcing_chat: null,
          },
        ],
        failed: [
          { row_index: 1, apollo_id: "person-partial", code: "invalid_candidate" },
        ],
        duplicates: [],
        original_session: {
          session_id: "thread-apollo",
          bound_person_id: null,
          reason: "multiple_selection",
        },
      })
      .mockResolvedValueOnce({
        status: "success",
        selected_row_count: 1,
        selected_identity_count: 1,
        kept: [
          {
            row_index: 0,
            apollo_id: "person-partial",
            person_id: "person-maya-file",
            version: 1,
            operation: "created",
            first_name: "Maya",
            last_name: null,
            last_name_status: "missing",
            title: null,
            company: "Apollo.io",
            sourcing_chat: null,
          },
        ],
        failed: [],
        duplicates: [],
        original_session: {
          session_id: "thread-apollo",
          bound_person_id: null,
          reason: "unbound",
        },
      });
    renderApollo();
    fireEvent.click(screen.getByRole("button", { name: "Searched Apollo · Completed" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Tim" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Maya" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Target for selected people" }), {
      target: { value: "Director-authored target" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review 2 selected candidates" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep 2 people" }));

    expect(
      await screen.findByText("Maya needs review (invalid candidate)."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", {
        name: "Open person file for Tim (surname hidden by Apollo)",
      }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry 1 failed candidate" }));

    await waitFor(() => expect(api.curateApolloCandidates).toHaveBeenCalledTimes(2));
    const retry = api.curateApolloCandidates.mock.calls[1]?.[0];
    expect(retry.people).toEqual([
      expect.objectContaining({ apolloId: "person-partial" }),
    ]);
    expect(retry.bindOriginal).toBe(false);
    expect(
      screen.getByRole("link", {
        name: "Open person file for Tim (surname hidden by Apollo)",
      }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("link", { name: "Open person file for Maya" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/needs review/)).not.toBeInTheDocument();
  });

  it("keeps the original search query beside the result count", () => {
    renderApollo();
    fireEvent.click(
      screen.getByRole("button", { name: "Searched Apollo · Completed" }),
    );

    expect(screen.getByText("2 candidates")).toBeInTheDocument();
    expect(screen.getByText("CEO at Apollo")).toBeInTheDocument();
  });

  it("uses candidate-shaped rows while an Apollo search is loading", () => {
    renderApollo(apolloSearch({ result: undefined }));

    const skeleton = screen.getByRole("list", {
      name: "Loading Apollo candidates",
    });
    expect(skeleton).toHaveAttribute("aria-busy", "true");
    expect(within(skeleton).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.queryByText("No Apollo matches")).not.toBeInTheDocument();
  });

  it("keeps Apollo failures contextual without exposing transport payloads", () => {
    const { container } = renderApollo(
      apolloSearch({
        isError: true,
        result: { error: "PRIVATE_APOLLO_TRANSPORT_PAYLOAD" },
        providerMetadata: {
          sourcecado: {
            failure: {
              summary: "Apollo couldn’t complete this people search.",
            },
          },
        },
      }),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Searched Apollo · Failed" }),
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Apollo couldn’t complete this people search.",
    );
    expect(container).not.toHaveTextContent("PRIVATE_APOLLO_TRANSPORT_PAYLOAD");
  });

  it("falls back safely for malformed legacy results and keeps payloads in Inspector", () => {
    const { container } = renderApollo(
      apolloSearch({ result: { people: "PRIVATE_LEGACY_APOLLO_PAYLOAD" } }),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Searched Apollo · Completed" }),
    );
    expect(
      screen.getByRole("heading", { name: "Apollo result needs review" }),
    ).toBeInTheDocument();
    expect(container).not.toHaveTextContent("PRIVATE_LEGACY_APOLLO_PAYLOAD");

    fireEvent.click(
      screen.getByRole("button", { name: "Inspect Searched Apollo" }),
    );
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(inspector).toHaveTextContent("PRIVATE_LEGACY_APOLLO_PAYLOAD");
  });

  it("bounds large shortlists behind a keyboard-operable show-more control", () => {
    renderApollo(
      apolloSearch({
        result: {
          people: Array.from({ length: 12 }, (_, index) => ({
            apolloId: `person-${index + 1}`,
            firstName: "Candidate",
            lastNameObfuscated: String(index + 1).padStart(2, "0"),
            title: "Engineer",
            organizationName: "Apollo.io",
            hasEmail: false,
          })),
        },
      }),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Searched Apollo · Completed" }),
    );
    const candidates = screen.getByRole("list", { name: "Apollo candidates" });
    expect(within(candidates).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.queryByText("Candidate 06")).not.toBeInTheDocument();

    const firstPage = screen.getByRole("button", {
      name: "Show 5 more candidates",
    });
    firstPage.focus();
    expect(firstPage).toHaveFocus();
    fireEvent.click(firstPage, { detail: 0 });
    expect(within(candidates).getAllByRole("listitem")).toHaveLength(10);

    fireEvent.click(
      screen.getByRole("button", { name: "Show 2 more candidates" }),
    );
    expect(within(candidates).getAllByRole("listitem")).toHaveLength(12);
    expect(
      screen.queryByRole("button", { name: /more candidates/ }),
    ).not.toBeInTheDocument();
  });

  it("opens stable candidate source and credit details in the provenance Inspector", () => {
    renderApollo();
    fireEvent.click(
      screen.getByRole("button", { name: "Searched Apollo · Completed" }),
    );

    expect(
      screen.getByText("Enrichment uses Apollo credits and requires approval."),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Inspect candidate Tim" }),
    );
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Tim",
    );
    expect(within(inspector).getByText("Apollo")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Inspect Apollo source" }));
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Apollo people search",
    );
    expect(within(inspector).getByText("2 candidates for CEO at Apollo")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Review Apollo credit use" }),
    );
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Apollo enrichment credits",
    );
    expect(inspector).toHaveTextContent("requires approval before execution");
  });

  it("renders an approved enriched contact with credit and partial-field state", () => {
    renderApollo(apolloEnrich());
    fireEvent.click(
      screen.getByRole("button", {
        name: "Enriched contact with Apollo · Completed",
      }),
    );

    expect(
      screen.getByRole("heading", { name: "Apollo enriched contact" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Tim Zheng")).toBeInTheDocument();
    expect(screen.getByText("CEO · Apollo.io")).toBeInTheDocument();
    expect(screen.getByText("tim@apollo.io")).toBeInTheDocument();
    expect(
      screen.getByText("Apollo credit used for this approved enrichment."),
    ).toBeInTheDocument();
    expect(screen.getByText("Missing phone", { exact: false })).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", {
        name: "Inspect enriched contact Tim Zheng",
      }),
    );
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Tim Zheng",
    );
    expect(within(inspector).getByRole("link", { name: "Open externally" })).toHaveAttribute(
      "href",
      "https://www.linkedin.com/in/timzheng",
    );
  });

  it("does not present Apollo enrichment as running before approval", () => {
    renderApollo(
      apolloEnrich({
        result: undefined,
        approval: {
          id: "approval-apollo-enrich",
          reason: "Apollo enrichment uses credits.",
        },
      }),
    );

    expect(
      screen.queryByRole("list", { name: "Loading Apollo candidates" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Apollo enriched contact" }),
    ).not.toBeInTheDocument();
  });

  it("renders a compact shortlist with safe candidate fields and explicit enrichment state", () => {
    const { container } = renderApollo();

    fireEvent.click(
      screen.getByRole("button", { name: "Searched Apollo · Completed" }),
    );

    expect(
      screen.getByRole("heading", { name: "Apollo shortlist" }),
    ).toBeInTheDocument();
    expect(screen.getByText("2 candidates")).toBeInTheDocument();
    expect(screen.getByText("Tim")).toBeInTheDocument();
    expect(screen.getByText("Surname hidden by Apollo")).toBeInTheDocument();
    expect(screen.getByText("CEO · Apollo.io")).toBeInTheDocument();
    expect(screen.getByText("Email available after enrichment")).toBeInTheDocument();
    expect(
      screen.getByText("Missing title and full name", { exact: false }),
    ).toBeInTheDocument();
    expect(container).not.toHaveTextContent("person-tim");
  });
});
