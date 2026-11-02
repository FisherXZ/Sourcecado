import { fireEvent, render, screen, within } from "@testing-library/react";
import type { SourceMessagePart, ToolCallMessagePart } from "@assistant-ui/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActivityGroup } from "../src/chat/ActivityGroup";
import { AnswerResults } from "../src/chat/AnswerResults";
import { Inspector, InspectorProvider } from "../src/chat/Inspector";
import { SourceChips } from "../src/chat/SourceCitation";
import { livingBrief } from "./livingBrief";

vi.mock("../src/api", () => ({
  curateApolloCandidates: vi.fn(),
  openPersonSourcingChat: vi.fn(),
}));

function call(
  toolName: string,
  overrides: Partial<ToolCallMessagePart> = {},
): ToolCallMessagePart {
  return {
    type: "tool-call",
    toolCallId: `call-${toolName}`,
    toolName,
    args: {},
    argsText: "{}",
    ...overrides,
  };
}

const apolloPeople = [
  {
    apolloId: "p1",
    firstName: "Tim",
    lastNameObfuscated: "Zh***g",
    title: "CEO",
    organizationName: "Apollo.io",
    hasEmail: true,
    directPhoneStatus: null,
  },
  {
    apolloId: "p2",
    firstName: "Maya",
    lastNameObfuscated: null,
    title: null,
    organizationName: "Apollo.io",
    hasEmail: false,
    directPhoneStatus: null,
  },
];

const apollo = (overrides: Partial<ToolCallMessagePart> = {}) =>
  call("apollo_search_people", {
    args: { organizationName: "Apollo", personTitles: ["CEO"] },
    result: { people: apolloPeople },
    ...overrides,
  });

const gmail = call("gmail_draft", {
  args: { to: "a@example.com", subject: "Hello", body: "Body text" },
  result: { id: "draft_1", to: "a@example.com", subject: "Hello", drafted: true, sent: false },
});

const calendar = call("calendar_list", {
  result: {
    events: [
      {
        id: "e1",
        summary: "Standup",
        start: { dateTime: "2026-08-25T10:00:00", timeZone: "America/Los_Angeles" },
        end: { dateTime: "2026-08-25T10:30:00", timeZone: "America/Los_Angeles" },
      },
    ],
  },
});

const drive = call("drive_search", {
  args: { query: "plan" },
  result: { files: [{ id: "f1", name: "Q3 plan", mimeType: "text/plain", stale: true }] },
});

const brief = call("board_get", {
  result: { status: "complete", partial: false, person_id: "p", brief: livingBrief() },
});

function renderAnswer(
  tools: ToolCallMessagePart[],
  sources: SourceMessagePart[] = [],
  steps = false,
) {
  return render(
    <InspectorProvider threadId="thread-cards">
      {steps ? <ActivityGroup tools={tools} messageState="complete" answerStarted /> : null}
      <AnswerResults tools={tools} sources={sources} />
      <Inspector />
    </InspectorProvider>,
  );
}

const failure = (cls: string) => ({
  sourcecado: {
    failure: {
      class: cls,
      connector_id: null,
      source: "Apollo",
      retry_safe: false,
      idempotent: false,
      summary: "Apollo is not allowed to do this.",
      repair_route: null,
      detail: "refused",
      call_id: "x",
      run_id: "r",
      session_id: "s",
      state: "failed",
    },
  },
});

describe("one chrome for every result card", () => {
  it("gives each renderer a header with provider icon, title and count, and a footer action with an icon", () => {
    const { container } = renderAnswer([apollo(), gmail, calendar, drive, brief]);
    const cards = [...container.querySelectorAll<HTMLElement>(".sourcecado-card")];
    expect(cards).toHaveLength(5);
    for (const card of cards) {
      expect(card.querySelector("header .sourcecado-card-provider svg")).not.toBeNull();
      expect(card.querySelector("header h3")?.textContent).toBeTruthy();
      const footer = card.querySelector("footer");
      expect(footer).not.toBeNull();
      expect(
        within(footer as HTMLElement).getByRole("button", { name: /^Open in inspector/ })
          .querySelector("svg"),
      ).not.toBeNull();
    }
    expect(screen.getByText("2 candidates")).toBeInTheDocument();
    expect(screen.getByText("1 event")).toBeInTheDocument();
    expect(screen.getByText("1 evidence item")).toBeInTheDocument();
  });

  it("gives every button in every card an icon", () => {
    const { container } = renderAnswer([apollo(), gmail, calendar, drive, brief]);
    const buttons = [...container.querySelectorAll<HTMLButtonElement>(".sourcecado-card button")];
    expect(buttons.length).toBeGreaterThan(5);
    for (const button of buttons) {
      // A row name is text, and it opens the inspector. Every other button has a glyph.
      if (button.classList.contains("sourcecado-card-name")) continue;
      expect(button.querySelector("svg"), button.textContent ?? "").not.toBeNull();
    }
  });

  it("draws each Apollo row with name, title, company and an email state, and never a raw payload", () => {
    const { container } = renderAnswer([apollo()]);
    const rows = within(screen.getByRole("list", { name: "Apollo candidates" })).getAllByRole(
      "listitem",
    );
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Tim");
    expect(rows[0]).toHaveTextContent("Surname hidden by Apollo");
    expect(rows[0]).toHaveTextContent("CEO · Apollo.io");
    expect(rows[0]).toHaveTextContent("Email available after enrichment");
    expect(rows[1]).toHaveTextContent("No title");
    expect(rows[1]).toHaveTextContent("Email availability unknown");
    expect(container).not.toHaveTextContent("Zh***g");
    expect(container.textContent).not.toMatch(/[{}]|apolloId/);
    // Keep stays behind a review step and is an icon button.
    expect(
      screen.getByRole("button", { name: "Review 0 selected candidates" }).querySelector("svg"),
    ).not.toBeNull();
  });

  it("shows a missing email as a gap and never invents an address", () => {
    const { container } = renderAnswer([
      call("apollo_enrich_contact", {
        result: { name: "Ada", title: "Founder", organizationName: "Analytic", email: null },
      }),
    ]);
    expect(screen.getAllByText("Not returned by Apollo").length).toBeGreaterThan(0);
    expect(container.textContent).not.toMatch(/@/);
  });
});

describe("card states", () => {
  const states: [string, string, () => ToolCallMessagePart[]][] = [
    ["loading", "Loading", () => [apollo({ result: undefined })]],
    ["empty", "Empty", () => [apollo({ result: { people: [] } })]],
    ["partial", "Partial", () => [apollo({ result: { people: apolloPeople, partial: true } })]],
    ["stale", "Stale", () => [drive]],
    [
      "restricted",
      "Restricted",
      () => [apollo({ result: undefined, isError: true, providerMetadata: failure("permission") })],
    ],
    [
      "failed",
      "Failed",
      () => [apollo({ result: undefined, isError: true, providerMetadata: failure("unknown") })],
    ],
    ["unsupported", "Unsupported", () => [apollo({ result: { people: "not a list" } })]],
  ];

  it.each(states)("shows %s as its own state with a word and an icon", (state, word, tools) => {
    const { container } = renderAnswer(tools());
    const card = container.querySelector(".sourcecado-card");
    expect(card).toHaveAttribute("data-state", state);
    const chip = within(card as HTMLElement).getByText(word);
    expect(chip.closest(".sourcecado-card-state")?.querySelector("svg")).not.toBeNull();
  });

  it("uses a different icon for every state", () => {
    const icons = states.map(([, , tools]) => {
      const { container, unmount } = renderAnswer(tools());
      const icon = container
        .querySelector(".sourcecado-card-state svg")
        ?.getAttribute("class");
      unmount();
      return icon;
    });
    expect(new Set(icons).size).toBe(states.length);
  });

  it("explains a failed card without printing the provider payload", () => {
    const { container } = renderAnswer([
      apollo({
        result: { error: "PRIVATE_PROVIDER_PAYLOAD" },
        isError: true,
        providerMetadata: failure("unknown"),
      }),
    ]);
    expect(screen.getByText("Apollo is not allowed to do this.")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("PRIVATE_PROVIDER_PAYLOAD");
  });

  it("reads an unknown shape as a sentence and keeps the record in the inspector", () => {
    const { container } = renderAnswer([
      call("calendar_list", { result: { weird: { nested: "PRIVATE_SHAPE" } } }),
    ]);
    const card = screen.getByRole("region", { name: "Calendar events" });
    expect(card).toHaveAttribute("data-state", "unsupported");
    expect(card).toHaveTextContent("Sourcecado cannot read this result.");
    expect(container).not.toHaveTextContent("PRIVATE_SHAPE");
    expect(container.textContent).not.toMatch(/[{}]/);
    fireEvent.click(screen.getByRole("button", { name: /^Open in inspector/ }));
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();
  });

  it("turns an evidence card that throws into an unsupported card too", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const result = { files: [{ id: "f1", name: "Plan" }] };
    Object.defineProperty(result.files[0], "name", {
      get() {
        throw new Error("boom");
      },
    });
    const { container } = renderAnswer([call("drive_search", { args: { query: "plan" }, result })]);
    spy.mockRestore();
    const card = screen.getByRole("region", { name: "Evidence" });
    expect(card).toHaveAttribute("data-state", "unsupported");
    expect(card).toHaveAttribute("data-card-tools", "call-drive_search");
    expect(card).toHaveTextContent("Sourcecado cannot read this result.");
    expect(container.textContent).not.toMatch(/[{}]/);
    expect(screen.getByRole("button", { name: /^Open in inspector/ })).toBeInTheDocument();
  });

  it("turns a card that throws into an unsupported card instead of a blank thread", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const person = { ...apolloPeople[0] };
    Object.defineProperty(person, "firstName", {
      get() {
        throw new Error("boom");
      },
    });
    renderAnswer([apollo({ result: { people: [person] } })]);
    spy.mockRestore();
    expect(screen.getByRole("region", { name: "Searched Apollo" })).toHaveAttribute(
      "data-state",
      "unsupported",
    );
  });
});

const threeSteps = () => [
  call("now", { result: { iso: "2026-10-26T13:00:00-07:00" } }),
  apollo(),
  call("board_query", { result: { people: [] } }),
];

describe("cards in the answer area", () => {
  afterEach(() => vi.restoreAllMocks());

  it("stay visible while the process layer is collapsed", () => {
    renderAnswer(threeSteps(), [], true);
    // Three steps group under one receipt, closed once the answer started.
    const receipt = screen.getByRole("button", { name: /^Used 3 sources/ });
    expect(receipt).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("region", { name: "Apollo shortlist" })).toBeVisible();
  });

  it("are reached from the step row with a Show result button that focuses the card heading", () => {
    Element.prototype.scrollIntoView = vi.fn();
    renderAnswer(threeSteps(), [], true);
    fireEvent.click(screen.getByRole("button", { name: /^Used 3 sources/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Searched Apollo/ }));
    fireEvent.click(screen.getByRole("button", { name: "Show result of Searched Apollo" }));
    expect(screen.getByRole("heading", { name: "Apollo shortlist" })).toHaveFocus();
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("show no Show result button for a step that has no card", () => {
    renderAnswer(threeSteps(), [], true);
    fireEvent.click(screen.getByRole("button", { name: /^Used 3 sources/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Checked current time/ }));
    expect(screen.queryByRole("button", { name: /Show result of Checked/ })).toBeNull();
  });
});

describe("a step that never finished", () => {
  it("gets no card after the turn ended, so no card reads Loading forever", () => {
    const stuck = apollo({ result: undefined });
    const { container, rerender } = render(
      <InspectorProvider threadId="thread-cards">
        <AnswerResults tools={[stuck]} messageState="running" />
      </InspectorProvider>,
    );
    expect(container.querySelector(".sourcecado-card")).toHaveAttribute("data-state", "loading");
    for (const ended of ["complete", "cancelled", "interrupted"]) {
      rerender(
        <InspectorProvider threadId="thread-cards">
          <AnswerResults tools={[stuck]} messageState={ended} />
        </InspectorProvider>,
      );
      expect(container.querySelector(".sourcecado-card"), ended).toBeNull();
    }
  });
});

describe("source chips", () => {
  const url = (id: string, address: string, title: string): SourceMessagePart => ({
    type: "source",
    sourceType: "url",
    id,
    url: address,
    title,
    providerMetadata: { sourcecado: { provider: "Tavily", stale: false, truncated: false } },
  });
  const doc: SourceMessagePart = {
    type: "source",
    sourceType: "document",
    id: "doc-1",
    title: "Q3 plan",
    providerMetadata: { sourcecado: { provider: "Google Drive", stale: true, truncated: false } },
  };

  it("render the domain with an icon, in one wrapping row", () => {
    renderAnswer(
      [],
      [url("s1", "https://www.example.com/news/story?x=1", "A story"), doc],
    );
    const list = screen.getByRole("list", { name: "Sources" });
    const chips = within(list).getAllByRole("button");
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent("example.com");
    expect(chips[0]).not.toHaveTextContent("www.");
    expect(chips[0]).toHaveAccessibleName("Source: A story (example.com)");
    expect(chips[0].querySelector("svg")).not.toBeNull();
    // A source with no URL shows its provider, and says when it is stale.
    expect(chips[1]).toHaveTextContent("Google Drive");
    expect(chips[1]).toHaveTextContent("Stale");
  });

  it("open the source in the inspector, where the external link is an icon", () => {
    renderAnswer([], [url("s1", "https://www.example.com/news/story", "A story")]);
    fireEvent.click(screen.getByRole("button", { name: /^Source: A story/ }));
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent("A story");
    const link = within(inspector).getByRole("link", { name: "Open externally" });
    expect(link).toHaveAttribute("href", "https://www.example.com/news/story");
    expect(link.querySelector("svg")).not.toBeNull();
    expect(link).toHaveTextContent("");
  });

  it("do not make an unsafe URL clickable", () => {
    renderAnswer([], [url("s1", "javascript:alert(1)", "Bad")]);
    fireEvent.click(screen.getByRole("button", { name: /^Source: Bad/ }));
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).queryByRole("link")).toBeNull();
  });

  it("show eight chips and open the rest on request", () => {
    const many = Array.from({ length: 11 }, (_, index) =>
      url(`s${index}`, `https://site${index}.example.org/page`, `Page ${index}`),
    );
    renderAnswer([], many);
    const list = screen.getByRole("list", { name: "Sources" });
    expect(within(list).getAllByRole("button")).toHaveLength(9);
    const more = screen.getByRole("button", { name: "Show 3 more" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    more.focus();
    fireEvent.click(more, { detail: 0 });
    expect(within(list).getAllByRole("button")).toHaveLength(12);
    // The toggle stays mounted, so focus stays on it. It now closes the row.
    const fewer = screen.getByRole("button", { name: "Show fewer" });
    expect(fewer).toHaveFocus();
    expect(fewer).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(fewer, { detail: 0 });
    expect(within(list).getAllByRole("button")).toHaveLength(9);
    expect(screen.getByRole("button", { name: "Show 3 more" })).toHaveFocus();
  });
});
