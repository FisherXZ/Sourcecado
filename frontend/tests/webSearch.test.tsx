import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { SourceMessagePart, ToolCallMessagePart } from "@assistant-ui/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActivityGroup } from "../src/chat/ActivityGroup";
import { AnswerResults } from "../src/chat/AnswerResults";
import { Inspector, InspectorProvider } from "../src/chat/Inspector";
import { evidenceReferences } from "../src/chat/protocol";
import { linkedPages } from "../src/chat/webSearch";
import { ChatPage } from "../src/routes/ChatPage";
import { styles } from "./cssBundle";

const api = vi.hoisted(() => ({
  getHealth: vi.fn(),
  getSessions: vi.fn(),
  getPersona: vi.fn(),
  getSchedule: vi.fn(),
  getGmail: vi.fn(),
  getConnectors: vi.fn(),
  getSettings: vi.fn(),
  getInbox: vi.fn(),
  getSession: vi.fn(),
  getCurrentRunTelemetry: vi.fn(),
  openChat: vi.fn(),
}));
let onChatEvent: ((event: Record<string, unknown>) => void) | undefined;

vi.mock("../src/api", () => ({
  connectCalendar: vi.fn(),
  connectDrive: vi.fn(),
  connectGmail: vi.fn(),
  connectGranola: vi.fn(),
  curateApolloCandidates: vi.fn(),
  disconnectGmail: vi.fn(),
  getHealth: api.getHealth,
  getSessions: api.getSessions,
  getPersona: api.getPersona,
  getSchedule: api.getSchedule,
  getGmail: api.getGmail,
  getConnectors: api.getConnectors,
  getSettings: api.getSettings,
  getInbox: api.getInbox,
  getSession: api.getSession,
  getCurrentRunTelemetry: api.getCurrentRunTelemetry,
  hasToken: () => true,
  openChat: api.openChat,
  openPersonSourcingChat: vi.fn(),
  resolveInbox: vi.fn(),
  runScheduleJob: vi.fn(),
  setPersona: vi.fn(),
}));

// --- fixtures -------------------------------------------------------------

type Row = { title: string; url: string; snippet: string };
const STORY: Row = {
  title: "Apollo raises a Series D",
  url: "https://www.techcrunch.com/2026/10/apollo-series-d/",
  snippet: "Apollo closed a $100M Series D led by a growth fund.",
};
const PROFILE: Row = {
  title: "Tim Zheng on LinkedIn",
  url: "https://www.linkedin.com/in/timzheng",
  snippet: "Tim Zheng is the CEO and cofounder of Apollo.",
};
const TALK: Row = {
  title: "Show HN: sourcing agents",
  url: "https://news.ycombinator.com/item?id=4242",
  snippet: "A thread about agents that rank candidates.",
};
const PIPELINE: Row = {
  title: "Internal sponsor pipeline",
  url: "https://sponsors.example.org/pipeline",
  snippet: "Sponsor names and amounts for the fall.",
};
const HANDBOOK: Row = {
  title: "Sponsor handbook preview",
  url: "https://docs.example.org/handbook",
  snippet: "Chapter 3 explains how sponsors are tiered.",
};
const PRESS: Row = {
  title: "Apollo press kit",
  url: "https://apollo.example.com/press",
  snippet: "Logos and a short company description.",
};
const BLANK: Row = {
  title: "Apollo on Wikipedia",
  url: "https://en.wikipedia.org/wiki/Apollo_(company)",
  snippet: "",
};

type Ref = Partial<{
  sensitivity: string;
  content: string;
  truncated: boolean;
}>;
const reference = (row: Row, over: Ref = {}, index = 0) => ({
  id: `ext_web_${row.url.length}_${index}`,
  origin: "external",
  trust: "untrusted_evidence",
  authority: "evidence_only",
  provider: "web",
  title: row.title,
  url: row.url.split("?")[0],
  sensitivity: "standard",
  content: "present",
  truncated: false,
  observed_at: "2026-10-27T09:00:00+00:00",
  source_time: null,
  body_chars: 100,
  ...over,
});

function search(
  id: string,
  query: string,
  rows: readonly Row[],
  refs: readonly Ref[] = [],
  overrides: Partial<ToolCallMessagePart> = {},
): ToolCallMessagePart {
  return {
    type: "tool-call",
    toolCallId: id,
    toolName: "web_search",
    args: { query },
    argsText: JSON.stringify({ query }),
    result: { results: rows },
    providerMetadata: {
      sourcecado: {
        evidence: rows.map((row, index) => reference(row, refs[index] ?? {}, index)),
      },
    },
    ...overrides,
  };
}

/** A search whose evidence list is written by hand, so its length can differ from the rows. */
function searchWithEvidence(
  id: string,
  rows: readonly Row[],
  evidence: readonly ReturnType<typeof reference>[],
): ToolCallMessagePart {
  return {
    type: "tool-call",
    toolCallId: id,
    toolName: "web_search",
    args: { query: "mismatch" },
    argsText: "{}",
    result: { results: rows },
    providerMetadata: { sourcecado: { evidence } },
  };
}

const drive = (): ToolCallMessagePart => ({
  type: "tool-call",
  toolCallId: "call-drive",
  toolName: "drive_search",
  args: { query: "plan" },
  argsText: "{}",
  result: { files: [{ id: "a" }] },
});

const failure = {
  class: "timeout_network",
  connector_id: null,
  source: "Web",
  retry_safe: false,
  idempotent: false,
  summary: "Web could not be reached. Review before retrying.",
  repair_route: null,
  detail: "timeout",
  call_id: "call-b",
  run_id: "run-1",
  session_id: "thread-1",
  state: "failed",
};

const button = (name: RegExp | string) => screen.getByRole("button", { name });

/** The inspector's raw JSON for the open item: the Output tab, then its Raw toggle. */
function openRaw() {
  const panel = screen.getByRole("complementary", { name: "Inspector" });
  fireEvent.click(within(panel).getByRole("tab", { name: "Output" }));
  fireEvent.click(within(panel).getByRole("button", { name: "Raw" }));
  return panel;
}

function renderLayer(
  tools: readonly ToolCallMessagePart[],
  extra: { messageState?: string; answerStarted?: boolean } = {},
) {
  return render(
    <InspectorProvider threadId="thread-1">
      <ActivityGroup tools={tools} messageState={extra.messageState ?? "complete"} {...extra} />
      <Inspector />
    </InspectorProvider>,
  );
}

function renderAnswer(
  tools: readonly ToolCallMessagePart[],
  answerText: string,
  sources: readonly SourceMessagePart[] = [],
) {
  return render(
    <InspectorProvider threadId="thread-1">
      <AnswerResults
        tools={tools}
        sources={sources}
        messageState="complete"
        answerText={answerText}
      />
      <Inspector />
    </InspectorProvider>,
  );
}

const THREE = [
  search("call-a", "apollo leadership", [STORY, PROFILE, TALK]),
  search("call-b", "sponsor pipeline", [PIPELINE, HANDBOOK], [
    { sensitivity: "restricted" },
    { content: "partial" },
  ]),
  search("call-c", "apollo background", [BLANK, PRESS, PROFILE, TALK], [
    { content: "absent" },
    { truncated: true, content: "partial" },
  ]),
];

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- the search row ---------------------------------------------------------

describe("web search row", () => {
  it("shows a run of searches as one row that opens to its queries", () => {
    renderLayer(THREE);
    // One row, no receipt line above it: it is the turn's only step row.
    const row = button(/^Searched the web/);
    expect(row).toHaveTextContent("3 queries");
    expect(row).toHaveTextContent("9 results");
    expect(row).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: /^Used/ })).toBeNull();
    expect(screen.queryByRole("list", { name: "Queries" })).toBeNull();

    fireEvent.click(row);
    const queries = within(screen.getByRole("list", { name: "Queries" })).getAllByRole("button");
    expect(queries).toHaveLength(3);
    expect(queries[0]).toHaveTextContent("apollo leadership");
    expect(queries[0]).toHaveTextContent("3 results");
    expect(queries[1]).toHaveTextContent("sponsor pipeline");
    expect(queries[1]).toHaveTextContent("2 results");
    expect(queries[2]).toHaveTextContent("apollo background");
    expect(queries[2]).toHaveTextContent("4 results");
    // A query is shut until the operator opens it.
    expect(queries[0]).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("list", { name: /^Results for/ })).toBeNull();
  });

  it("opens a query to its retained results with title and domain, as text and not as links", () => {
    const { container } = renderLayer(THREE);
    fireEvent.click(button(/^Searched the web/));
    fireEvent.click(button(/^apollo leadership/));
    const results = within(screen.getByRole("list", { name: "Results for apollo leadership" }));
    const rows = results.getAllByRole("button");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveAccessibleName("Result: Apollo raises a Series D (techcrunch.com)");
    expect(rows[0]).toHaveTextContent("Apollo raises a Series D");
    expect(rows[0]).toHaveTextContent("techcrunch.com");
    expect(rows[0]).not.toHaveTextContent("www.");
    expect(rows[1]).toHaveTextContent("linkedin.com");
    expect(rows[0].querySelector("svg")).not.toBeNull();
    // Nothing in the layer is an external link. The inspector holds that.
    expect(container.querySelector("a")).toBeNull();
    // The other queries stay shut.
    expect(screen.queryByRole("list", { name: "Results for sponsor pipeline" })).toBeNull();
  });

  it("shows truncated, partial, restricted and missing states on the result", () => {
    renderLayer(THREE);
    fireEvent.click(button(/^Searched the web/));
    fireEvent.click(button(/^sponsor pipeline/));
    fireEvent.click(button(/^apollo background/));
    const restricted = screen.getByRole("button", { name: /^Result: Internal sponsor pipeline/ });
    expect(restricted).toHaveAccessibleName(
      "Result: Internal sponsor pipeline (sponsors.example.org), Restricted",
    );
    expect(restricted).toHaveTextContent("Restricted");
    expect(
      screen.getByRole("button", { name: /^Result: Sponsor handbook preview/ }),
    ).toHaveTextContent("Partial");
    const missing = screen.getByRole("button", { name: /^Result: Apollo on Wikipedia/ });
    expect(missing).toHaveTextContent("No excerpt");
    const truncated = screen.getByRole("button", { name: /^Result: Apollo press kit/ });
    expect(truncated).toHaveTextContent("Truncated");
    expect(truncated).toHaveTextContent("Partial");
  });

  it("fails closed when the rows and the evidence records do not line up and one record is restricted", () => {
    // Three result rows, two evidence records. The record for PROFILE is missing,
    // and nothing says which row the restricted record was written for.
    const tools = [
      searchWithEvidence(
        "call-a",
        [STORY, PROFILE, PIPELINE],
        [reference(STORY, {}, 0), reference(PIPELINE, { sensitivity: "restricted" }, 2)],
      ),
    ];
    renderAnswer(tools, `${STORY.url} ${PROFILE.url} ${PIPELINE.url}`);
    const chips = within(screen.getByRole("list", { name: "Sources" })).getAllByRole("button");
    expect(chips.map((chip) => chip.getAttribute("aria-label"))).toEqual([
      "Source: Apollo raises a Series D (techcrunch.com), Unverified",
      "Source: Tim Zheng on LinkedIn (linkedin.com), Unverified",
      "Source: Internal sponsor pipeline (sponsors.example.org), Restricted",
    ]);
    for (const chip of chips) {
      fireEvent.click(chip);
      const inspector = screen.getByRole("complementary", { name: "Inspector" });
      for (const row of [STORY, PROFILE, PIPELINE]) {
        expect(inspector).not.toHaveTextContent(row.snippet);
      }
    }
    expect(screen.getByRole("complementary", { name: "Inspector" })).toHaveTextContent(
      "Restricted source. The excerpt is not shown.",
    );
    fireEvent.click(chips[1]!);
    expect(screen.getByRole("complementary", { name: "Inspector" })).toHaveTextContent(
      "Unverified source. The excerpt is not shown.",
    );
  });

  it("withholds every snippet of such a call from the raw Inspect view too", () => {
    renderLayer([
      searchWithEvidence(
        "call-a",
        [STORY, PROFILE, PIPELINE],
        [reference(STORY, {}, 0), reference(PIPELINE, { sensitivity: "restricted" }, 2)],
      ),
    ]);
    fireEvent.click(button(/^Searched the web/));
    fireEvent.click(button("Inspect search for mismatch"));
    const inspector = openRaw();
    for (const row of [STORY, PROFILE, PIPELINE]) {
      expect(inspector).not.toHaveTextContent(row.snippet);
    }
  });

  it("still matches by page when the counts differ, and withholds only the restricted row", () => {
    // Two rows, three records. Every row finds its own record, so nothing is guessed.
    renderAnswer(
      [
        searchWithEvidence(
          "call-a",
          [STORY, PIPELINE],
          [
            reference(PIPELINE, { sensitivity: "restricted" }, 2),
            reference(STORY, {}, 0),
            reference(HANDBOOK, {}, 3),
          ],
        ),
      ],
      `${STORY.url} ${PIPELINE.url}`,
    );
    fireEvent.click(button(/^Source: Apollo raises a Series D/));
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(inspector).toHaveTextContent(STORY.snippet);
    fireEvent.click(button(/^Source: Internal sponsor pipeline/));
    expect(inspector).not.toHaveTextContent(PIPELINE.snippet);
    expect(inspector).toHaveTextContent("Restricted source.");
  });

  it("guesses no state when the counts differ and no record is restricted", () => {
    renderAnswer(
      [searchWithEvidence("call-a", [STORY, PROFILE, PIPELINE], [reference(STORY, {}, 0)])],
      `${PROFILE.url}`,
    );
    const chip = button(/^Source: Tim Zheng on LinkedIn/);
    expect(chip).toHaveAccessibleName("Source: Tim Zheng on LinkedIn (linkedin.com)");
    fireEvent.click(chip);
    expect(screen.getByRole("complementary", { name: "Inspector" })).toHaveTextContent(
      PROFILE.snippet,
    );
  });

  it("does not warn about duplicate keys when one query returns the same page twice", () => {
    const warn = vi.spyOn(console, "error").mockImplementation(() => {});
    // The backend gives the same page and title the same evidence id.
    renderLayer([searchWithEvidence("call-a", [STORY, STORY], [reference(STORY), reference(STORY)])]);
    fireEvent.click(button(/^Searched the web/));
    expect(screen.getAllByRole("button", { name: /^Result: Apollo raises a Series D/ })).toHaveLength(2);
    expect(warn.mock.calls.flat().join(" ")).not.toMatch(/same key/);
    warn.mockRestore();
  });

  it("treats a stored URL with a user name or password as not safe: no domain, no link, no chip", () => {
    const secret: Row = {
      title: "Private page",
      url: "https://admin:hunter2@creds.example.com/secret/page?token=abc",
      snippet: "A page behind a login.",
    };
    const tools = [search("call-a", "creds", [secret])];
    const layer = renderLayer(tools);
    fireEvent.click(button(/^Searched the web/));
    const row = button(/^Result: Private page/);
    expect(row).toHaveAccessibleName("Result: Private page (Link not safe)");
    expect(layer.container).not.toHaveTextContent("hunter2");
    fireEvent.click(row);
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).queryByRole("link")).toBeNull();
    expect(inspector).toHaveTextContent("External URL unavailable");
    expect(inspector).not.toHaveTextContent("hunter2");
    layer.unmount();
    // The answer cannot make it a chip either.
    renderAnswer(tools, secret.url);
    expect(screen.queryByRole("list", { name: "Sources" })).toBeNull();
  });

  it("never shows a password from a stored URL in the raw Inspect view, in a row or in an excerpt", () => {
    const secret: Row = {
      title: "Page at https://bob:hunter2@site.example.com/a",
      url: "https://admin:hunter2@creds.example.com/secret/page?email=a@b.example#frag",
      snippet: "Log in at http://root:hunter2@intranet.example.org/login or see https://ok.example.com/x?u=a@b.example.",
    };
    const tool = search("call-a", "find https://me:hunter2@host.example.com/q", [secret]);
    const stored = JSON.stringify(tool);
    const { container } = renderLayer([tool]);
    fireEvent.click(button(/^Searched the web/));
    // The row, the query line and the label show no password.
    expect(container).not.toHaveTextContent("hunter2");
    fireEvent.click(button("Inspect search for find https://host.example.com/q"));
    const inspector = openRaw();
    expect(inspector).not.toHaveTextContent("hunter2");
    expect(inspector).not.toHaveTextContent("admin:");
    // Only the user info goes. The rest of the URL, and an "@" in a query, stay.
    expect(inspector).toHaveTextContent("https://creds.example.com/secret/page?email=a@b.example#frag");
    expect(inspector).toHaveTextContent("https://ok.example.com/x?u=a@b.example");
    // The arguments, in Context, show no password either.
    fireEvent.click(within(inspector).getByRole("tab", { name: "Context" }));
    expect(inspector).toHaveTextContent("https://host.example.com/q");
    expect(inspector).not.toHaveTextContent("hunter2");
    // The stored call is not changed.
    expect(JSON.stringify(tool)).toBe(stored);
    expect(stored).toContain("hunter2");
  });

  it("shows an evidence state this build does not know as Unverified, never as clean", () => {
    renderLayer([
      search("call-a", "q", [STORY, PROFILE], [{ content: "constructor" }, { content: "ambiguous" }]),
    ]);
    fireEvent.click(button(/^Searched the web/));
    expect(button(/^Result: Apollo raises a Series D/)).toHaveTextContent("Unverified");
    expect(button(/^Result: Tim Zheng on LinkedIn/)).toHaveTextContent("Unverified");
  });

  it("says No excerpt for a result that came back with no snippet, even with no evidence record", () => {
    renderLayer([search("call-a", "q", [BLANK])]);
    fireEvent.click(button(/^Searched the web/));
    expect(button(/^Result: Apollo on Wikipedia/)).toHaveTextContent("No excerpt");
  });

  it("opens a lone query by itself and names it in the row", () => {
    renderLayer([search("call-a", "northwind funding", [STORY])]);
    const row = button(/^Searched the web/);
    expect(row).toHaveTextContent("for “northwind funding”");
    fireEvent.click(row);
    expect(button(/^northwind funding/)).toHaveAttribute("aria-expanded", "true");
    expect(button(/^Result: Apollo raises a Series D/)).toBeInTheDocument();
  });

  it("says when a query returned nothing, or kept no result list", () => {
    renderLayer([
      search("call-a", "nothing here", []),
      search("call-b", "old copy", [], [], { result: { sourcecado: { sources: [] } } }),
    ]);
    fireEvent.click(button(/^Searched the web/));
    const queries = within(screen.getByRole("list", { name: "Queries" })).getAllByRole("button");
    expect(queries[0]).toHaveTextContent("No results");
    // The old saved copy has no result list, so no count is claimed for it.
    expect(queries[1]).not.toHaveTextContent(/result/);
    fireEvent.click(queries[0]);
    fireEvent.click(queries[1]);
    expect(screen.getByText("The search returned no results.")).toBeInTheDocument();
    expect(screen.getByText("No result list is saved for this query.")).toBeInTheDocument();
  });

  it("keeps a failed search as its own step with its recovery controls", () => {
    renderLayer([
      search("call-a", "good one", [STORY]),
      search("call-b", "bad one", [], [], {
        result: { error: "timeout" },
        isError: true,
        providerMetadata: { sourcecado: { failure } },
      }),
      search("call-c", "good two", [PROFILE]),
    ]);
    fireEvent.click(button(/^Used 3 sources/));
    // The two good searches are two rows around the failed step. The failed
    // one is the ordinary step row, with its state word and its recovery menu.
    expect(screen.getAllByRole("button", { name: /^Searched the web/ })).toHaveLength(2);
    const failed = button(/^Searching the web for “bad one”/);
    expect(failed).toHaveTextContent("Could not connect");
    // A failed step that needs the operator opens by itself.
    expect(failed).toHaveAttribute("aria-expanded", "true");
    // A web search is not safe to repeat, so Retry is off and says why.
    expect(screen.getByText(/Retry is off\./)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^More recovery options for Searching the web/ }),
    ).toBeInTheDocument();
  });

  it("shows a search that is still running with its own state and nothing to open yet", () => {
    renderLayer([search("call-a", "still going", [], [], { result: undefined })], {
      messageState: "running",
    });
    const row = button(/^Searching the web/);
    expect(row).toHaveTextContent("Running");
    fireEvent.click(row);
    const list = screen.getByRole("list", { name: "Queries" });
    expect(within(list).queryByRole("button")).toBeNull();
    expect(list).toHaveTextContent("still going");
  });

  it("collapses into the turn receipt like any other step", () => {
    renderLayer([search("call-a", "q", [STORY, PROFILE]), drive()], { answerStarted: true });
    const receipt = button(/^Used 2 sources/);
    expect(receipt).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: /^Searched the web/ })).toBeNull();
    fireEvent.click(receipt);
    expect(button(/^Searched the web/)).toBeInTheDocument();
    expect(button(/^Searched Drive/)).toBeInTheDocument();
  });

  it("does not hand a restricted snippet back through the raw inspect view", () => {
    renderLayer(THREE);
    fireEvent.click(button(/^Searched the web/));
    fireEvent.click(button(/^sponsor pipeline/));
    fireEvent.click(button("Inspect search for sponsor pipeline"));
    const inspector = openRaw();
    expect(inspector).not.toHaveTextContent(PIPELINE.snippet);
    expect(inspector).toHaveTextContent("(withheld: not shown)");
    // The other result in the same call keeps its snippet.
    expect(inspector).toHaveTextContent(HANDBOOK.snippet);
  });

  it("keeps the withheld excerpt out of the panel when the open item follows its step", () => {
    const tool = searchWithEvidence(
      "call-a",
      [STORY, PIPELINE],
      [reference(STORY, {}, 0), reference(PIPELINE, { sensitivity: "restricted" }, 2)],
    );
    const layer = (tools: readonly ToolCallMessagePart[]) => (
      <InspectorProvider threadId="thread-1">
        <ActivityGroup tools={tools} messageState="complete" />
        <Inspector />
      </InspectorProvider>
    );
    const { rerender } = render(layer([tool]));
    fireEvent.click(button(/^Searched the web/));
    fireEvent.click(button("Inspect search for mismatch"));
    // Another step joins the turn. The open item follows, and is rebuilt.
    rerender(layer([tool, drive()]));
    const inspector = openRaw();
    expect(inspector).toHaveTextContent(STORY.snippet);
    expect(inspector).not.toHaveTextContent(PIPELINE.snippet);
    expect(inspector).toHaveTextContent("(withheld: not shown)");
  });

  it("keeps a way to inspect the raw call for each query", () => {
    renderLayer(THREE);
    fireEvent.click(button(/^Searched the web/));
    fireEvent.click(button(/^sponsor pipeline/));
    fireEvent.click(button("Inspect search for sponsor pipeline"));
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Searched the web",
    );
  });
});

// --- cited sources ----------------------------------------------------------

describe("cited web sources", () => {
  const answer = `The CEO is Tim Zheng ([LinkedIn](${PROFILE.url})). The round is reported at ${STORY.url}.`;

  it("shows chips for the results the answer links, and none for the rest", () => {
    renderAnswer(THREE, answer);
    const chips = within(screen.getByRole("list", { name: "Sources" })).getAllByRole("button");
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveAccessibleName("Source: Apollo raises a Series D (techcrunch.com)");
    expect(chips[1]).toHaveAccessibleName("Source: Tim Zheng on LinkedIn (linkedin.com)");
    expect(chips[0].querySelector("svg")).not.toBeNull();
    // Retrieved in a query but never linked: not a chip.
    expect(screen.queryByRole("button", { name: /Show HN/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Sponsor handbook/ })).toBeNull();
  });

  it("shows no chips when the answer links nothing", () => {
    renderAnswer(THREE, "Tim Zheng leads Apollo. I did not link anything.");
    expect(screen.queryByRole("list", { name: "Sources" })).toBeNull();
  });

  it("shows a result once when two queries returned it", () => {
    renderAnswer(THREE, answer);
    // PROFILE comes back in two queries. It is one chip.
    expect(
      screen.getAllByRole("button", { name: /^Source: Tim Zheng on LinkedIn/ }),
    ).toHaveLength(1);
  });

  it("opens the retained source in the inspector with its excerpt, and never fetches", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const before = JSON.stringify(THREE);
    renderAnswer(THREE, answer);
    fireEvent.click(button(/^Source: Tim Zheng on LinkedIn/));
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Tim Zheng on LinkedIn",
    );
    expect(within(inspector).getByText(PROFILE.snippet)).toBeInTheDocument();
    const link = within(inspector).getByRole("link", { name: "Open externally" });
    expect(link).toHaveAttribute("href", PROFILE.url);
    expect(link.querySelector("svg")).not.toBeNull();
    // The stored tool calls are exactly what they were.
    expect(JSON.stringify(THREE)).toBe(before);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("opens a retrieved result that was not cited, from the search row, with its excerpt", () => {
    renderLayer(THREE);
    fireEvent.click(button(/^Searched the web/));
    fireEvent.click(button(/^apollo leadership/));
    fireEvent.click(button(/^Result: Show HN/));
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByText(TALK.snippet)).toBeInTheDocument();
    expect(within(inspector).getByRole("link", { name: "Open externally" })).toHaveAttribute(
      "href",
      TALK.url,
    );
  });

  it("shows a restricted source's state on the chip and withholds its excerpt", () => {
    renderAnswer(THREE, `Pipeline notes: ${PIPELINE.url}`);
    const chip = button(/^Source: Internal sponsor pipeline/);
    expect(chip).toHaveAccessibleName(
      "Source: Internal sponsor pipeline (sponsors.example.org), Restricted",
    );
    expect(chip).toHaveTextContent("Restricted");
    fireEvent.click(chip);
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByText(/Restricted source\. The excerpt is not shown\./)).toBeInTheDocument();
    expect(inspector).not.toHaveTextContent(PIPELINE.snippet);
  });

  it("shows partial, truncated and missing sources as what they are", () => {
    renderAnswer(THREE, `${HANDBOOK.url} ${BLANK.url}`);
    const partial = button(/^Source: Sponsor handbook preview/);
    expect(partial).toHaveTextContent("Partial");
    fireEvent.click(partial);
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(inspector).toHaveTextContent("Partial source. Only part of it was retained.");
    expect(inspector).toHaveTextContent(HANDBOOK.snippet);

    fireEvent.click(button(/^Source: Apollo on Wikipedia/));
    expect(inspector).toHaveTextContent("No excerpt was retained for this result.");
  });

  it("shows a truncated source with the inspector's Truncated badge", () => {
    renderAnswer(THREE, `Background: ${PRESS.url}`);
    const chip = button(/^Source: Apollo press kit/);
    expect(chip).toHaveTextContent("Truncated");
    fireEvent.click(chip);
    expect(
      within(screen.getByRole("complementary", { name: "Inspector" })).getByText("Truncated"),
    ).toBeInTheDocument();
  });

  it("keeps the state when one query returned a clean copy and another a flagged copy", () => {
    const tools = [
      search("call-a", "first", [STORY]),
      search("call-b", "second", [STORY], [{ truncated: true, content: "partial" }]),
    ];
    renderAnswer(tools, STORY.url);
    const chips = within(screen.getByRole("list", { name: "Sources" })).getAllByRole("button");
    expect(chips).toHaveLength(1);
    expect(chips[0]).toHaveTextContent("Truncated");
  });

  it("does not turn a result with an unsafe or missing URL into a link or a chip", () => {
    const unsafe: Row = { title: "Bad link", url: "javascript:alert(1)", snippet: "Not a web page." };
    const none: Row = { title: "No link", url: "", snippet: "Came back with no URL." };
    const tools = [search("call-a", "odd", [unsafe, none])];
    renderAnswer(tools, "javascript:alert(1) is mentioned but it is not a web link.");
    // No chip: only an http(s) page can be cited.
    expect(screen.queryByRole("list", { name: "Sources" })).toBeNull();
  });

  it("shows an unsafe URL in the row as text, and the inspector draws no link for it", () => {
    const unsafe: Row = { title: "Bad link", url: "javascript:alert(1)", snippet: "Not a web page." };
    const none: Row = { title: "No link", url: "", snippet: "Came back with no URL." };
    const { container } = renderLayer([search("call-a", "odd", [unsafe, none])]);
    fireEvent.click(button(/^Searched the web/));
    const unsafeRow = button(/^Result: Bad link/);
    expect(unsafeRow).toHaveAccessibleName("Result: Bad link (Link not safe)");
    expect(button(/^Result: No link/)).toHaveAccessibleName("Result: No link (No link)");
    fireEvent.click(unsafeRow);
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).queryByRole("link")).toBeNull();
    expect(inspector).toHaveTextContent("External URL unavailable");
    expect(container.querySelector("a[href^='javascript']")).toBeNull();
  });

  it("keeps chips from other tools beside the cited web results", () => {
    const doc: SourceMessagePart = {
      type: "source",
      sourceType: "document",
      id: "doc-1",
      title: "Q3 plan",
      mediaType: "text/plain",
      providerMetadata: { sourcecado: { provider: "Google Drive", stale: false, truncated: false } },
    };
    renderAnswer(THREE, answer, [doc]);
    const chips = within(screen.getByRole("list", { name: "Sources" })).getAllByRole("button");
    expect(chips).toHaveLength(3);
    expect(chips[0]).toHaveTextContent("Google Drive");
  });

  it("does not repeat a web page an upstream source already shows", () => {
    const upstream: SourceMessagePart = {
      type: "source",
      sourceType: "url",
      id: "upstream",
      url: PROFILE.url,
      title: "Tim Zheng on LinkedIn",
      providerMetadata: { sourcecado: { provider: "Web", stale: false, truncated: false } },
    };
    renderAnswer(THREE, answer, [upstream]);
    expect(
      within(screen.getByRole("list", { name: "Sources" })).getAllByRole("button"),
    ).toHaveLength(2);
  });
});

describe("what counts as a link in the answer", () => {
  it("reads markdown links, bare URLs, parenthesised URLs and trailing punctuation", () => {
    const pages = linkedPages(
      [
        "[one](https://a.example.com/one) and https://b.example.com/two.",
        "(see https://c.example.com/three), then **https://d.example.com/four**",
        "A page with brackets: https://en.wikipedia.org/wiki/Apollo_(company).",
        "[https://e.example.com/five](https://e.example.com/five)",
      ].join("\n"),
    );
    expect([...pages].sort()).toEqual([
      "a.example.com/one",
      "b.example.com/two",
      "c.example.com/three",
      "d.example.com/four",
      "e.example.com/five",
      "en.wikipedia.org/wiki/Apollo_(company)",
    ]);
  });

  it("treats www, a trailing slash, the scheme and a fragment as the same page, and a query as another", () => {
    const pages = linkedPages("http://www.example.com/page/#top and https://example.com/item?id=1");
    expect(pages.has("example.com/page")).toBe(true);
    expect(pages.has("example.com/item?id=1")).toBe(true);
    expect(pages.has("example.com/item?id=2")).toBe(false);
    expect(linkedPages("javascript:alert(1) ftp://example.com/file").size).toBe(0);
  });
});

describe("evidence references", () => {
  it("copies the known fields and drops a row with no id", () => {
    expect(
      evidenceReferences([
        { id: "a", provider: "web", title: "T", url: "https://x.example/", sensitivity: "restricted", content: "partial", truncated: true, body: "SECRET" },
        { title: "no id" },
        "junk",
      ]),
    ).toEqual([
      {
        id: "a",
        provider: "web",
        title: "T",
        url: "https://x.example/",
        sensitivity: "restricted",
        content: "partial",
        truncated: true,
      },
    ]);
    expect(evidenceReferences(undefined)).toEqual([]);
  });
});

// --- through the real thread -------------------------------------------------

const envelope = {
  version: 2,
  session_id: "thread-alpha",
  run_id: "run-web",
  message_id: "message-web",
  part_id: "part-web",
} as const;

const withIds = (events: Record<string, unknown>[]) =>
  events.map((event, index) => ({ ...envelope, event_id: `web-${index}`, ...event }));

function finished(id: string, rows: readonly Row[], refs: readonly Ref[] = []) {
  return {
    type: "tool_finished",
    id,
    name: "web_search",
    ok: true,
    result: { results: rows },
    evidence: rows.map((row, index) => reference(row, refs[index] ?? {}, index)),
  };
}

describe("web search in the thread", () => {
  beforeEach(() => {
    window.localStorage.clear();
    onChatEvent = undefined;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn() },
    });
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    api.getHealth.mockResolvedValue({ status: "ok", piece: "desktop", slice: 1, model: "m" });
    api.getSessions.mockResolvedValue({ sessions: [], open_id: "thread-alpha" });
    api.getPersona.mockResolvedValue({ id: "sourcing", name: "Sourcing Operator", tools: [] });
    api.getSchedule.mockResolvedValue({ jobs: [], runs: [] });
    api.getGmail.mockResolvedValue({ connected: false, email: null });
    api.getConnectors.mockResolvedValue({ connectors: [] });
    api.getSettings.mockResolvedValue({
      persona: { id: "sourcing", name: "Sourcing Operator" },
      model: "m",
      gmail: { connected: false, email: null },
      apollo: { configured: false },
    });
    api.getInbox.mockResolvedValue({ items: [] });
    api.getCurrentRunTelemetry.mockResolvedValue({
      version: 1,
      session_id: "thread-alpha",
      current_run: null,
    });
    api.openChat.mockImplementation((callback) => {
      onChatEvent = callback;
      return {
        send: vi.fn(),
        approve: vi.fn(),
        cancel: vi.fn(),
        queue: vi.fn(),
        recover: vi.fn(),
        close: vi.fn(),
      };
    });
  });

  it("restores the search row, the cited chips and the stored states from the event log", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const text = `Zheng is the CEO ([LinkedIn](${PROFILE.url})). The funding is at ${STORY.url}.`;
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Web",
      messages: [],
      queue: [],
      queue_paused: false,
      events: withIds([
        { type: "turn_start", state: "running" },
        { type: "tool_started", id: "call-1", name: "web_search", arguments: { query: "apollo leadership" } },
        finished("call-1", [STORY, PROFILE, TALK], [{ truncated: true, content: "partial" }]),
        { type: "tool_started", id: "call-2", name: "web_search", arguments: { query: "sponsor pipeline" } },
        finished("call-2", [PIPELINE], [{ sensitivity: "restricted" }]),
        { type: "assistant_delta", delta: text },
        { type: "turn_end", state: "complete", text },
      ]),
    });

    render(<ChatPage sessionId="thread-alpha" />);

    const row = await screen.findByRole("button", { name: /^Searched the web/ });
    expect(row).toHaveTextContent("2 queries");
    expect(row).toHaveTextContent("4 results");
    // Only the two linked results are chips.
    const chips = within(await screen.findByRole("list", { name: "Sources" })).getAllByRole("button");
    expect(chips.map((chip) => chip.getAttribute("aria-label"))).toEqual([
      "Source: Apollo raises a Series D (techcrunch.com), Truncated, Partial",
      "Source: Tim Zheng on LinkedIn (linkedin.com)",
    ]);

    // The stored states come from the event's evidence record.
    fireEvent.click(row);
    fireEvent.click(button(/^apollo leadership/));
    fireEvent.click(button(/^sponsor pipeline/));
    expect(button(/^Result: Apollo raises a Series D/)).toHaveTextContent("Truncated");
    expect(button(/^Result: Internal sponsor pipeline/)).toHaveTextContent("Restricted");
    expect(button(/^Result: Show HN/)).not.toHaveTextContent(/Truncated|Partial|Restricted/);

    // A chip opens the retained source. Nothing is fetched.
    fireEvent.click(chips[1]!);
    const inspector = screen.getByRole("complementary", { name: "Inspector" });
    expect(within(inspector).getByText(PROFILE.snippet)).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("shows a search while it runs and the row once it finishes", async () => {
    api.getSession.mockResolvedValue({
      id: "thread-alpha",
      title: "Live",
      messages: [],
      queue: [],
      queue_paused: false,
      events: [],
    });
    render(<ChatPage sessionId="thread-alpha" />);
    await waitFor(() => expect(onChatEvent).toBeDefined());
    const send = (event: Record<string, unknown>, index: number) =>
      act(() => onChatEvent!({ ...envelope, event_id: `live-${index}`, ...event }));

    send({ type: "turn_start", state: "running" }, 0);
    send({ type: "tool_started", id: "call-1", name: "web_search", arguments: { query: "apollo leadership" } }, 1);
    const running = await screen.findByRole("button", { name: /^Searching the web/ });
    expect(running).toHaveTextContent("Running");

    send(finished("call-1", [STORY, PROFILE]), 2);
    const done = await screen.findByRole("button", { name: /^Searched the web/ });
    expect(done).toHaveTextContent("2 results");
    // A second search joins the same row.
    send({ type: "tool_started", id: "call-2", name: "web_search", arguments: { query: "apollo funding" } }, 3);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^Searching the web/ })).toHaveTextContent("2 queries"),
    );
    expect(screen.getAllByRole("button", { name: /web/ })).toHaveLength(1);
  });
});

// --- styles ------------------------------------------------------------------

describe("web search styles", () => {
  it("keeps rows touch-sized on narrow screens and stacks the domain under the title", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)\s*\{\s*\.sourcecado-search-detail\s*\{[^}]*\}[\s\S]*?\.sourcecado-search-query-line,\s*\.sourcecado-search-result\s*\{[^}]*min-height:\s*44px;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-search-result\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*16px minmax\(0, 1fr\);/,
    );
  });

  it("bounds the open row and lets a receipt body do the scrolling when there is one", () => {
    expect(styles).toMatch(
      /\.sourcecado-search-detail\s*\{[^}]*max-height:\s*min\(320px, 40vh\);[^}]*overflow-y:\s*auto;/,
    );
    expect(styles).toMatch(
      /\.sourcecado-activity-body \.sourcecado-search-detail\s*\{[^}]*max-height:\s*none;/,
    );
  });

  it("lets the search row label wrap on narrow screens and keeps the query count whole", () => {
    expect(styles).toMatch(
      /@media \(max-width: 767px\)[\s\S]*?\.sourcecado-search > \.sourcecado-step-toggle \.sourcecado-step-label\s*\{[^}]*-webkit-line-clamp:\s*2;[^}]*white-space:\s*normal;/,
    );
    expect(styles).toMatch(/\.sourcecado-search-count\s*\{[^}]*white-space:\s*nowrap;/);
    expect(
      screen.queryByText("3 queries") ?? (renderLayer(THREE), screen.getByText("3 queries")),
    ).toHaveClass("sourcecado-search-count");
  });

  it("sits inside the process layer block, so the 13px and muted-ink rules cover it", () => {
    const start = styles.indexOf("/* Process layer (#226)");
    const end = styles.indexOf("/* End of the process layer (#226) */");
    const search = styles.indexOf("/* Web search (#252)");
    expect(search).toBeGreaterThan(start);
    expect(search).toBeLessThan(end);
  });
});
