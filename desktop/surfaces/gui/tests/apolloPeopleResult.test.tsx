import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ToolCallMessagePart } from "@assistant-ui/react";
import { describe, expect, it } from "vitest";

import { ActivityGroup } from "../src/chat/ActivityGroup";
import { Inspector, InspectorProvider } from "../src/chat/Inspector";

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
      screen.getByRole("button", { name: "Inspect candidate Tim Zh***g" }),
    );
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Tim Zh***g",
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
    expect(screen.getByText("Tim Zh***g")).toBeInTheDocument();
    expect(screen.getByText("CEO · Apollo.io")).toBeInTheDocument();
    expect(screen.getByText("Email available after enrichment")).toBeInTheDocument();
    expect(
      screen.getByText("Missing title and full name", { exact: false }),
    ).toBeInTheDocument();
    expect(container).not.toHaveTextContent("person-tim");
  });
});
