import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { SourceMessagePart, ToolCallMessagePart } from "@assistant-ui/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActivityGroup } from "../src/chat/ActivityGroup";
import { AnswerResults } from "../src/chat/AnswerResults";
import { RecoveryProvider } from "../src/chat/recovery";
import {
  INSPECTOR_DEFAULT_WIDTH,
  INSPECTOR_MAX_WIDTH,
  INSPECTOR_MIN_WIDTH,
  Inspector,
  InspectorProvider,
  clampInspectorWidth,
  useInspector,
  type InspectorTarget,
} from "../src/chat/Inspector";
import {
  formatClock,
  formatDuration,
  formatTiming,
  humanizeKey,
  maskUrlCredentials,
} from "../src/chat/inspectorFormat";

const WIDTH_KEY = "sourcecado.inspector.width.v1";

const tool = (overrides: Partial<ToolCallMessagePart> = {}): ToolCallMessagePart => ({
  type: "tool-call",
  toolCallId: "call-board",
  toolName: "board_query",
  args: { personTitles: ["ML founder"], organization_name: "Berkeley" },
  argsText: "{}",
  result: { people: [], count: 0, rawNote: "PRIVATE_BOARD_RAW" },
  ...overrides,
});

const at = (hours: number, minutes: number, seconds: number) =>
  new Date(2026, 9, 26, hours, minutes, seconds).getTime();

function renderTurn(tools: ToolCallMessagePart[], messageState: unknown = "complete") {
  const tree = (list: ToolCallMessagePart[], state: unknown) => (
    <InspectorProvider threadId="thread-1">
      <ActivityGroup tools={list} messageState={state} />
      <Inspector />
    </InspectorProvider>
  );
  const view = render(tree(tools, messageState));
  return {
    ...view,
    update: (list: ToolCallMessagePart[], state: unknown) => view.rerender(tree(list, state)),
  };
}

/** Opens the process layer if it is grouped, then the step, then its Inspect button. */
function openInspector(stepName: RegExp, inspectName: string) {
  const receipt = document.querySelector<HTMLElement>(".sourcecado-activity-receipt");
  if (receipt && receipt.getAttribute("aria-expanded") !== "true") fireEvent.click(receipt);
  fireEvent.click(screen.getByRole("button", { name: stepName }));
  const inspect = screen.getByRole("button", { name: inspectName });
  fireEvent.click(inspect);
  return inspect;
}

const inspectorPanel = () => screen.getByRole("complementary", { name: "Inspector" });

describe("Inspector panel for a tool step", () => {
  beforeEach(() => {
    window.localStorage.removeItem(WIDTH_KEY);
  });
  afterEach(() => {
    window.localStorage.removeItem(WIDTH_KEY);
    vi.useRealTimers();
  });

  it("opens with the step icon, title, kind label, status chip and an icon close button", () => {
    renderTurn([tool({ timing: { startedAt: at(14, 2, 11), completedAt: at(14, 2, 11) + 800 } })]);
    openInspector(/^Queried Board/, "Inspect Queried Board");

    const panel = inspectorPanel();
    const heading = within(panel).getByRole("heading", { level: 2 });
    expect(heading).toHaveTextContent("Queried Board");
    expect(heading).toHaveFocus();
    expect(panel.querySelector(".sourcecado-inspector-kind-icon svg")).not.toBeNull();
    expect(panel).toHaveTextContent("Tool result");
    expect(panel.querySelector(".sourcecado-inspector-chip")).toHaveTextContent("Done");
    // The old eyebrow is gone.
    expect(panel).not.toHaveTextContent(/tool detail/i);

    const close = within(panel).getByRole("button", { name: "Close inspector" });
    expect(close).toHaveTextContent("");
    expect(close.querySelector("svg")).not.toBeNull();

    const tabs = within(panel).getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Progress", "Output", "Context"]);
  });

  it("shows timing as a clock time and a duration, never as epoch milliseconds", () => {
    const startedAt = at(14, 2, 11);
    renderTurn([tool({ timing: { startedAt, completedAt: startedAt + 800 } })]);
    openInspector(/^Queried Board/, "Inspect Queried Board");

    fireEvent.click(within(inspectorPanel()).getByRole("tab", { name: "Progress" }));
    expect(inspectorPanel()).toHaveTextContent("Started 14:02:11 · 0.8s");
    expect(inspectorPanel()).not.toHaveTextContent(String(startedAt));
  });

  it("lists every step of the turn in Progress with its state and duration", () => {
    const base = at(9, 30, 0);
    renderTurn([
      tool({
        toolCallId: "call-now",
        toolName: "now",
        args: {},
        result: { iso: "2026-10-26T09:30:00" },
        timing: { startedAt: base, completedAt: base + 120 },
      }),
      tool({
        toolCallId: "call-board",
        timing: { startedAt: base + 200, completedAt: base + 2_700 },
      }),
      tool({
        toolCallId: "call-skill",
        toolName: "load_skill",
        args: {},
        result: undefined,
        isError: true,
        timing: { startedAt: base + 3_000, completedAt: base + 14_000 },
      }),
    ]);
    openInspector(/^Queried Board/, "Inspect Queried Board");

    fireEvent.click(within(inspectorPanel()).getByRole("tab", { name: "Progress" }));
    const steps = within(
      within(inspectorPanel()).getByRole("list", { name: "Steps in this turn" }),
    ).getAllByRole("listitem");
    expect(steps).toHaveLength(3);
    expect(steps[0]).toHaveTextContent("Checked current time");
    expect(steps[0]).toHaveTextContent("0.1s");
    expect(steps[1]).toHaveTextContent("Queried Board");
    expect(steps[1]).toHaveTextContent("2.5s");
    expect(steps[1]).toHaveAttribute("aria-current", "step");
    expect(steps[2]).toHaveTextContent("11s");
    expect(steps[2]).not.toHaveAttribute("aria-current");
  });

  it("opens a running step on Progress, follows it to Done and stays on the tab the operator is on", () => {
    const startedAt = at(14, 2, 11);
    const running = tool({ result: undefined, timing: { startedAt } });
    const view = renderTurn([running], "running");
    openInspector(/^Querying Board/, "Inspect Queried Board");

    expect(within(inspectorPanel()).getByRole("tab", { name: "Progress" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(inspectorPanel().querySelector(".sourcecado-inspector-chip")).toHaveTextContent(
      "Running",
    );
    expect(inspectorPanel()).toHaveTextContent("Started 14:02:11");

    view.update([tool({ timing: { startedAt, completedAt: startedAt + 1_500 } })], "complete");
    expect(inspectorPanel().querySelector(".sourcecado-inspector-chip")).toHaveTextContent("Done");
    expect(inspectorPanel()).toHaveTextContent("Started 14:02:11 · 1.5s");
    expect(within(inspectorPanel()).getByRole("tab", { name: "Progress" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("shows the card first on Output and the raw JSON only behind the Raw toggle", () => {
    const draft = tool({
      toolCallId: "call-gmail",
      toolName: "gmail_draft",
      args: { to: "alyssa@example.com", subject: "Sourcing update" },
      result: {
        id: "draft_1",
        to: "alyssa@example.com",
        subject: "Sourcing update",
        drafted: true,
        sent: false,
        rawMime: "PRIVATE_RAW_MIME",
      },
    });
    renderTurn([draft]);
    openInspector(/^Prepared Gmail draft/, "Inspect Prepared Gmail draft");

    const panel = inspectorPanel();
    expect(within(panel).getByRole("tab", { name: "Output" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(within(panel).getByRole("heading", { name: "Gmail draft" })).toBeInTheDocument();
    expect(panel).not.toHaveTextContent("PRIVATE_RAW_MIME");

    const raw = within(panel).getByRole("button", { name: "Raw" });
    expect(raw).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(raw);
    expect(raw).toHaveAttribute("aria-pressed", "true");
    expect(panel).toHaveTextContent("PRIVATE_RAW_MIME");
    expect(within(panel).queryByRole("heading", { name: "Gmail draft" })).not.toBeInTheDocument();

    fireEvent.click(within(panel).getByRole("button", { name: "Card" }));
    expect(panel).not.toHaveTextContent("PRIVATE_RAW_MIME");
  });

  it("reads a result with no card as a table, with the JSON behind the toggle", () => {
    renderTurn([tool()]);
    openInspector(/^Queried Board/, "Inspect Queried Board");

    const panel = inspectorPanel();
    const summary = within(panel).getByRole("button", { name: "Summary" });
    expect(summary).toHaveAttribute("aria-pressed", "true");
    const raw = within(panel).getByRole("row", { name: /Raw note/ });
    expect(raw).toHaveTextContent("PRIVATE_BOARD_RAW");
    expect(panel).not.toHaveTextContent('"rawNote"');
    fireEvent.click(within(panel).getByRole("button", { name: "Raw" }));
    expect(panel).toHaveTextContent('"rawNote"');
  });

  it("shows the arguments as a readable table in Context", () => {
    renderTurn([tool()]);
    openInspector(/^Queried Board/, "Inspect Queried Board");

    fireEvent.click(within(inspectorPanel()).getByRole("tab", { name: "Context" }));
    const panel = inspectorPanel();
    expect(within(panel).getByRole("heading", { name: "Arguments" })).toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /Person titles.*ML founder/ })).toBeInTheDocument();
    expect(
      within(panel).getByRole("row", { name: /Organization name.*Berkeley/ }),
    ).toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /Tool.*board_query/ })).toBeInTheDocument();
    // A table, not a JSON dump.
    expect(panel).not.toHaveTextContent('"personTitles"');
  });

  it("closes on Escape and returns focus to the Inspect button that opened it", async () => {
    renderTurn([tool()]);
    const inspect = openInspector(/^Queried Board/, "Inspect Queried Board");
    expect(screen.getByRole("complementary", { name: "Inspector" })).toBeInTheDocument();

    // Escape works from inside the panel too.
    within(inspectorPanel()).getByRole("tab", { name: "Context" }).focus();
    fireEvent.keyDown(document.activeElement ?? window, { key: "Escape" });

    expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
    await waitFor(() => expect(inspect).toHaveFocus());
  });

  it("leaves Escape to a control that already used it, such as an open menu", () => {
    render(
      <InspectorProvider threadId="thread-1">
        <ActivityGroup tools={[tool()]} messageState="complete" />
        <input
          aria-label="Menu field"
          onKeyDown={(event) => {
            if (event.key === "Escape") event.preventDefault();
          }}
        />
        <Inspector />
      </InspectorProvider>,
    );
    openInspector(/^Queried Board/, "Inspect Queried Board");

    const field = screen.getByRole("textbox", { name: "Menu field" });
    field.focus();
    fireEvent.keyDown(field, { key: "Escape" });
    expect(inspectorPanel()).toBeInTheDocument();

    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
  });

  it("moves between tabs with the arrow keys", () => {
    renderTurn([tool()]);
    openInspector(/^Queried Board/, "Inspect Queried Board");

    const output = within(inspectorPanel()).getByRole("tab", { name: "Output" });
    output.focus();
    fireEvent.keyDown(output, { key: "ArrowRight" });
    const context = within(inspectorPanel()).getByRole("tab", { name: "Context" });
    expect(context).toHaveAttribute("aria-selected", "true");
    expect(context).toHaveFocus();
    fireEvent.keyDown(context, { key: "ArrowRight" });
    expect(within(inspectorPanel()).getByRole("tab", { name: "Progress" })).toHaveFocus();
  });
});

describe("Inspector panel width", () => {
  beforeEach(() => {
    window.localStorage.removeItem(WIDTH_KEY);
  });
  afterEach(() => {
    window.localStorage.removeItem(WIDTH_KEY);
  });

  const widthOf = () =>
    inspectorPanel().style.getPropertyValue("--inspector-width");

  it("clamps a width between the minimum and what the workspace can spare", () => {
    expect(clampInspectorWidth(100, 1200)).toBe(INSPECTOR_MIN_WIDTH);
    expect(clampInspectorWidth(5000, 5000)).toBe(INSPECTOR_MAX_WIDTH);
    // The thread keeps 440px, so a 900px workspace allows 460px at most.
    expect(clampInspectorWidth(700, 900)).toBe(460);
    // A workspace too small for both still gives the panel its minimum.
    expect(clampInspectorWidth(700, 600)).toBe(INSPECTOR_MIN_WIDTH);
  });

  it("resizes by keyboard on the separator and remembers the width for the next open", () => {
    const first = renderTurn([tool()]);
    openInspector(/^Queried Board/, "Inspect Queried Board");
    expect(widthOf()).toBe(`${INSPECTOR_DEFAULT_WIDTH}px`);

    const separator = within(inspectorPanel()).getByRole("separator", {
      name: "Resize inspector",
    });
    expect(separator).toHaveAttribute("aria-valuenow", String(INSPECTOR_DEFAULT_WIDTH));
    fireEvent.keyDown(separator, { key: "ArrowLeft" });
    expect(widthOf()).toBe(`${INSPECTOR_DEFAULT_WIDTH + 16}px`);
    fireEvent.keyDown(separator, { key: "ArrowLeft", shiftKey: true });
    expect(widthOf()).toBe(`${INSPECTOR_DEFAULT_WIDTH + 16 + 48}px`);
    expect(window.localStorage.getItem(WIDTH_KEY)).toBe(String(INSPECTOR_DEFAULT_WIDTH + 64));

    // A fresh mount (the next visit) opens at the remembered width.
    first.unmount();
    renderTurn([tool()]);
    openInspector(/^Queried Board/, "Inspect Queried Board");
    expect(widthOf()).toBe(`${INSPECTOR_DEFAULT_WIDTH + 64}px`);

    fireEvent.doubleClick(
      within(inspectorPanel()).getByRole("separator", { name: "Resize inspector" }),
    );
    expect(widthOf()).toBe(`${INSPECTOR_DEFAULT_WIDTH}px`);
  });

  it("resizes by dragging the left edge and stores the width when the drag ends", () => {
    renderTurn([tool()]);
    openInspector(/^Queried Board/, "Inspect Queried Board");
    const panel = inspectorPanel();
    vi.spyOn(panel, "getBoundingClientRect").mockReturnValue({
      width: 400,
      height: 800,
      top: 0,
      left: 1000,
      right: 1400,
      bottom: 800,
      x: 1000,
      y: 0,
      toJSON: () => ({}),
    });
    const separator = within(panel).getByRole("separator", { name: "Resize inspector" });
    separator.setPointerCapture = vi.fn();
    // jsdom has no PointerEvent, so the drag events would lose their coordinates.
    class TestPointerEvent extends MouseEvent {
      readonly pointerId: number;
      constructor(type: string, init: MouseEventInit & { pointerId?: number } = {}) {
        super(type, init);
        this.pointerId = init.pointerId ?? 1;
      }
    }
    Object.defineProperty(window, "PointerEvent", {
      configurable: true,
      value: TestPointerEvent,
    });

    fireEvent.pointerDown(separator, { button: 0, pointerId: 1, clientX: 1000 });
    fireEvent.pointerMove(separator, { pointerId: 1, clientX: 900 });
    // Dragging left makes the panel wider.
    expect(widthOf()).toBe("500px");
    fireEvent.pointerMove(separator, { pointerId: 1, clientX: 1100 });
    expect(widthOf()).toBe("320px");
    fireEvent.pointerUp(separator, { pointerId: 1, clientX: 1100 });
    expect(window.localStorage.getItem(WIDTH_KEY)).toBe("320");
    Reflect.deleteProperty(window, "PointerEvent");
  });

  it("ignores a stored width that is out of range", () => {
    window.localStorage.setItem(WIDTH_KEY, "40");
    renderTurn([tool()]);
    openInspector(/^Queried Board/, "Inspect Queried Board");
    expect(widthOf()).toBe(`${INSPECTOR_DEFAULT_WIDTH}px`);
  });
});

function KindHarness({ items }: { readonly items: readonly InspectorTarget[] }) {
  const { select } = useInspector();
  return (
    <>
      {items.map((item) => (
        <button key={item.id} type="button" onClick={(event) => select(item, event.currentTarget)}>
          {`Open ${item.id}`}
        </button>
      ))}
      <Inspector />
    </>
  );
}

describe("Inspector item kinds", () => {
  const items: InspectorTarget[] = [
    {
      kind: "tool",
      id: "tool",
      title: "Queried Board",
      status: "success",
      state: "done",
      provider: "Sourcecado Board",
      toolName: "board_query",
      args: { query: "ML founders" },
      result: { count: 2 },
      externalUrl: "https://example.com/board",
      steps: [{ id: "tool", label: "Queried Board", icon: "person", state: "done" }],
    },
    {
      kind: "source",
      id: "source",
      title: "Codeology brief",
      status: "success",
      provider: "Google Drive",
      preview: "A one page brief.",
      externalUrl: "https://drive.google.com/file/codeology",
    },
    {
      kind: "file",
      id: "file",
      title: "targets.csv",
      status: "success",
      preview: "42 rows",
    },
  ];

  it("labels a tool result, a source and an attached file with distinct labels and icons", () => {
    render(
      <InspectorProvider threadId="thread-kinds">
        <KindHarness items={items} />
      </InspectorProvider>,
    );

    const seen = new Map<string, { label: string; icon: string }>();
    for (const item of items) {
      fireEvent.click(screen.getByRole("button", { name: `Open ${item.id}` }));
      const panel = inspectorPanel();
      seen.set(item.id, {
        label: panel.querySelector(".sourcecado-inspector-kind")?.textContent ?? "",
        icon: panel.querySelector(".sourcecado-inspector-kind-icon")?.innerHTML ?? "",
      });
      expect(within(panel).getByRole("heading", { level: 2 })).toHaveTextContent(item.title);
    }
    expect([...seen.values()].map((entry) => entry.label)).toEqual([
      "Tool result",
      "Source",
      "Attached file",
    ]);
    expect(new Set([...seen.values()].map((entry) => entry.icon)).size).toBe(3);
  });

  it("gives a tool result its tabs and a source or file one scrolling view", () => {
    render(
      <InspectorProvider threadId="thread-kinds">
        <KindHarness items={items} />
      </InspectorProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open tool" }));
    expect(within(inspectorPanel()).getAllByRole("tab")).toHaveLength(3);
    // Provider and the source link are in Context.
    fireEvent.click(within(inspectorPanel()).getByRole("tab", { name: "Context" }));
    expect(within(inspectorPanel()).getByRole("row", { name: /Provider.*Sourcecado Board/ })).toBeInTheDocument();
    expect(within(inspectorPanel()).getByRole("link", { name: "Open externally" })).toHaveAttribute(
      "href",
      "https://example.com/board",
    );

    fireEvent.click(screen.getByRole("button", { name: "Open source" }));
    expect(within(inspectorPanel()).queryByRole("tab")).not.toBeInTheDocument();
    expect(inspectorPanel()).toHaveTextContent("A one page brief.");
    expect(inspectorPanel()).toHaveTextContent("Google Drive");

    fireEvent.click(screen.getByRole("button", { name: "Open file" }));
    expect(within(inspectorPanel()).queryByRole("tab")).not.toBeInTheDocument();
    expect(inspectorPanel()).toHaveTextContent("42 rows");
  });

  it("keeps the thread scroll and the composer draft while it opens and closes", async () => {
    render(
      <InspectorProvider threadId="thread-keep">
        <div role="log" data-testid="thread" />
        <textarea aria-label="Draft" />
        <KindHarness items={items} />
      </InspectorProvider>,
    );
    const thread = screen.getByTestId("thread");
    thread.scrollTop = 96;
    const draft = screen.getByRole("textbox", { name: "Draft" });
    fireEvent.change(draft, { target: { value: "Reply to Alyssa tomorrow" } });
    draft.focus();

    const opener = screen.getByRole("button", { name: "Open source" });
    fireEvent.click(opener);
    expect(thread.scrollTop).toBe(96);
    expect(draft).toHaveValue("Reply to Alyssa tomorrow");

    await act(async () => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(thread.scrollTop).toBe(96);
    expect(draft).toHaveValue("Reply to Alyssa tomorrow");
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("shows a failed step with its reason and no tabs for an item that failed to load", () => {
    render(
      <InspectorProvider threadId="thread-fail">
        <KindHarness
          items={[
            {
              kind: "file",
              id: "broken",
              title: "notes.pdf",
              status: "error",
              errorSummary: "This file couldn’t be read.",
              retry: vi.fn(),
            },
          ]}
        />
      </InspectorProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open broken" }));
    const panel = inspectorPanel();
    expect(within(panel).getByRole("alert")).toHaveTextContent("This file couldn’t be read.");
    expect(panel.querySelector(".sourcecado-inspector-chip")).toHaveTextContent("Failed");
    expect(within(panel).getByRole("button", { name: "Retry detail" })).toBeInTheDocument();
  });
});

describe("Inspector with the result cards and source chips of the thread", () => {
  const weird = tool({
    toolCallId: "call-calendar",
    toolName: "calendar_list",
    args: { days: 7 },
    result: { weird: { nested: "PRIVATE_SHAPE" } },
  });

  function renderThread(tools: ToolCallMessagePart[], sources: SourceMessagePart[] = []) {
    return render(
      <InspectorProvider threadId="thread-cards">
        <ActivityGroup tools={tools} messageState="complete" answerStarted />
        <AnswerResults tools={tools} sources={sources} messageState="complete" />
        <Inspector />
      </InspectorProvider>,
    );
  }

  it("shows a copy of the card without the thread card's anchor, and hides a button that would reopen the item", () => {
    renderThread([weird]);
    const threadCard = screen.getByRole("region", { name: "Calendar events" });
    expect(threadCard).toHaveAttribute("id", "sourcecado-card-call-calendar");
    expect(threadCard).toHaveAttribute("data-card-tools", "call-calendar");

    fireEvent.click(screen.getByRole("button", { name: /^Checked calendar/ }));
    fireEvent.click(screen.getByRole("button", { name: "Inspect Checked calendar" }));

    const panel = inspectorPanel();
    const copy = within(panel).getByRole("region", { name: "Calendar events" });
    expect(copy).toHaveAttribute("data-state", "unsupported");
    expect(copy).not.toHaveAttribute("id");
    expect(copy).not.toHaveAttribute("data-card-tools");
    expect(within(panel).queryByRole("button", { name: /^Open in inspector/ })).toBeNull();
    // The thread card keeps its button and its anchor.
    expect(screen.getAllByRole("region", { name: "Calendar events" })[0]).toBe(threadCard);
    expect(within(threadCard).getByRole("button", { name: /^Open in inspector/ })).toBeInTheDocument();
    expect(panel).not.toHaveTextContent("PRIVATE_SHAPE");
    fireEvent.click(within(panel).getByRole("button", { name: "Raw" }));
    expect(panel).toHaveTextContent("PRIVATE_SHAPE");
  });

  it("opens the same panel, with a Progress tab, from a card's Open in inspector button", () => {
    renderThread([weird]);
    fireEvent.click(
      within(screen.getByRole("region", { name: "Calendar events" })).getByRole("button", {
        name: /^Open in inspector/,
      }),
    );
    const panel = inspectorPanel();
    expect(within(panel).getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Progress",
      "Output",
      "Context",
    ]);
    expect(panel).toHaveTextContent("Tool result");
  });

  it("hides a password in a source address, as a link and as text", () => {
    const source: SourceMessagePart = {
      type: "source",
      sourceType: "url",
      id: "s-creds",
      url: "https://user:secret@creds.example.com/x",
      title: "With credentials",
      providerMetadata: { sourcecado: { provider: "Tavily", stale: false, truncated: false } },
    };
    renderThread([], [source]);
    fireEvent.click(screen.getByRole("button", { name: /^Source: With credentials/ }));
    const panel = inspectorPanel();
    expect(within(panel).queryByRole("link")).toBeNull();
    expect(panel).toHaveTextContent("External URL unavailable");
    expect(panel.innerHTML).not.toContain("secret");
  });

  it("opens a source chip as a Source item with its provider and an icon link", () => {
    const source: SourceMessagePart = {
      type: "source",
      sourceType: "url",
      id: "s1",
      url: "https://www.example.com/news/story",
      title: "A story",
      providerMetadata: { sourcecado: { provider: "Tavily", stale: false, truncated: true } },
    };
    renderThread([], [source]);
    const chip = screen.getByRole("button", { name: /^Source: A story/ });
    fireEvent.click(chip);

    const panel = inspectorPanel();
    expect(within(panel).getByRole("heading", { level: 2 })).toHaveTextContent("A story");
    expect(panel.querySelector(".sourcecado-inspector-kind")).toHaveTextContent("Source");
    expect(within(panel).queryByRole("tab")).not.toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /Provider.*Tavily/ })).toBeInTheDocument();
    expect(within(panel).getByText("Truncated")).toBeInTheDocument();
    const link = within(panel).getByRole("link", { name: "Open externally" });
    expect(link).toHaveAttribute("href", "https://www.example.com/news/story");
    expect(link).toHaveTextContent("");
    expect(within(panel).getByRole("row", { name: /Source.*example\.com/ })).toBeInTheDocument();
  });
});

describe("Inspector panel follow-ups from verification", () => {
  const failure = (cls: string) => ({
    sourcecado: {
      failure: {
        class: cls,
        connector_id: null,
        source: "Board",
        retry_safe: false,
        idempotent: false,
        summary: "Board could not run this query.",
        repair_route: null,
        detail: "RAW_FAILURE_DETAIL",
        call_id: "x",
        run_id: "r",
        session_id: "s",
        state: "failed",
      },
    },
  });

  beforeEach(() => {
    window.localStorage.removeItem(WIDTH_KEY);
  });
  afterEach(() => {
    window.localStorage.removeItem(WIDTH_KEY);
    vi.unstubAllGlobals();
    Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
  });

  it("shows a failed step as a failure class and counts, with the provider text only in Raw", () => {
    renderTurn([
      tool({
        isError: true,
        providerMetadata: failure("timeout_network"),
        result: {
          error: 'Error: http 502 {"upstream":"RAWPAYLOAD-8841"}',
          message: "Bad gateway from upstream",
          count: 3,
          rows: [{ a: 1 }, { a: 2 }],
          tags: ["x", "y"],
          body: `{"long":"${"z".repeat(200)}"}`,
        },
      }),
    ]);
    // A failed step opens by itself, so its Inspect button is already there.
    fireEvent.click(screen.getByRole("button", { name: "Inspect Queried Board" }));

    const panel = inspectorPanel();
    expect(within(panel).getByRole("alert")).toHaveTextContent("Board could not run this query.");
    expect(within(panel).getByRole("row", { name: /Failure.*Timeout or network/ })).toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /Failed in.*Board/ })).toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /Count.*3/ })).toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /Rows.*2 items/ })).toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /Tags.*x, y/ })).toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /In Raw.*3 more fields/ })).toBeInTheDocument();
    expect(panel).not.toHaveTextContent("RAWPAYLOAD");
    expect(panel).not.toHaveTextContent("Bad gateway");
    expect(panel).not.toHaveTextContent("RAW_FAILURE_DETAIL");
    expect(panel.textContent).not.toMatch(/[{}]/);

    fireEvent.click(within(panel).getByRole("button", { name: "Raw" }));
    expect(panel).toHaveTextContent("RAWPAYLOAD-8841");
  });

  it("gives a tool step a Provider row, and a long title a tooltip", () => {
    renderTurn([
      tool({
        toolCallId: "call-apollo",
        toolName: "apollo_enrich_contact",
        args: { firstName: "Tim" },
        result: { name: "Tim Zheng" },
      }),
    ]);
    openInspector(/^Enriched contact with Apollo/, "Inspect Enriched contact with Apollo");
    const panel = inspectorPanel();
    fireEvent.click(within(panel).getByRole("tab", { name: "Context" }));
    expect(within(panel).getByRole("row", { name: /Provider.*Apollo/ })).toBeInTheDocument();
    expect(within(panel).getByRole("heading", { level: 2 })).toHaveAttribute(
      "title",
      within(panel).getByRole("heading", { level: 2 }).textContent,
    );
  });

  it("shows an address with a password as https://***@host in Context, Summary and Raw, and keeps the stored event", () => {
    const step = tool({
      args: {
        endpoint: "https://alice:SECRETPW123@api.example.com/v1?token=abc",
        note: "Use https://carol@mail.example.org/inbox, not http://dave:SECRETPW123@plain.example.net.",
        mirrors: ["https://erin:SECRETPW123@m1.example.com/a"],
      },
      result: {
        link: "https://bob:SECRETPW123@files.example.com/report.pdf",
        count: 2,
        nested: { url: "https://frank:SECRETPW123@deep.example.com/x" },
        // An @ in a path or a query is not user info.
        contact: "https://example.com/team?email=lee@example.com",
      },
    });
    renderTurn([step]);
    openInspector(/^Queried Board/, "Inspect Queried Board");
    const panel = inspectorPanel();

    // Output, Summary view.
    expect(within(panel).getByRole("row", { name: /Link.*https:\/\/\*\*\*@files\.example\.com\/report\.pdf/ })).toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /Nested · Url.*https:\/\/\*\*\*@deep\.example\.com\/x/ })).toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /Contact.*email=lee@example\.com/ })).toBeInTheDocument();
    expect(panel).not.toHaveTextContent("SECRETPW123");

    // Output, Raw view.
    fireEvent.click(within(panel).getByRole("button", { name: "Raw" }));
    expect(panel).toHaveTextContent("https://***@files.example.com/report.pdf");
    expect(panel).not.toHaveTextContent("SECRETPW123");
    expect(panel).not.toHaveTextContent("bob:");

    // Context arguments.
    fireEvent.click(within(panel).getByRole("tab", { name: "Context" }));
    expect(within(panel).getByRole("row", { name: /Endpoint.*https:\/\/\*\*\*@api\.example\.com\/v1\?token=abc/ })).toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /Note.*Use https:\/\/\*\*\*@mail\.example\.org\/inbox, not http:\/\/\*\*\*@plain\.example\.net\./ })).toBeInTheDocument();
    expect(within(panel).getByRole("row", { name: /Mirrors.*https:\/\/\*\*\*@m1\.example\.com\/a/ })).toBeInTheDocument();
    expect(panel).not.toHaveTextContent("SECRETPW123");
    expect(panel.innerHTML).not.toContain("SECRETPW123");

    // Display only: the stored tool call keeps its own values.
    expect((step.args as { endpoint: string }).endpoint).toContain("SECRETPW123");
    expect((step.result as { link: string }).link).toContain("SECRETPW123");
  });

  it("holds a stored width to the room the workspace has, on load", () => {
    window.localStorage.setItem(WIDTH_KEY, String(INSPECTOR_MAX_WIDTH));
    // A 900px workspace leaves 460px for the panel and keeps 440px for the thread.
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
      configurable: true,
      get: () => 900,
    });
    renderTurn([tool()]);
    openInspector(/^Queried Board/, "Inspect Queried Board");
    const panel = inspectorPanel();
    expect(panel.style.getPropertyValue("--inspector-width")).toBe("460px");
    expect(
      within(panel).getByRole("separator", { name: "Resize inspector" }),
    ).toHaveAttribute("aria-valuenow", "460");
    // The choice is kept. A wider window gives the panel its stored width back.
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
      configurable: true,
      get: () => 1400,
    });
    fireEvent(window, new Event("resize"));
    expect(panel.style.getPropertyValue("--inspector-width")).toBe(`${INSPECTOR_MAX_WIDTH}px`);
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
      configurable: true,
      get: () => 1000,
    });
    fireEvent(window, new Event("resize"));
    expect(panel.style.getPropertyValue("--inspector-width")).toBe("560px");
  });

  it("makes the thread inert behind a full-screen panel, and frees it before focus returns", async () => {
    const listeners = new Set<() => void>();
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation(() => ({
        matches: true,
        addEventListener: (_: string, listener: () => void) => listeners.add(listener),
        removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
      })),
    );
    const { container } = renderTurn([tool()]);
    const inspect = openInspector(/^Queried Board/, "Inspect Queried Board");

    const thread = container.querySelector("section.sourcecado-activity")!;
    expect(thread).toHaveAttribute("inert");
    expect(inspectorPanel()).not.toHaveAttribute("inert");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(thread).not.toHaveAttribute("inert");
    await waitFor(() => expect(inspect).toHaveFocus());
    expect(listeners.size).toBe(0);
  });

  it("keeps Tab inside a full-screen panel", () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation(() => ({
        matches: true,
        addEventListener: () => {},
        removeEventListener: () => {},
      })),
    );
    renderTurn([tool()]);
    openInspector(/^Queried Board/, "Inspect Queried Board");
    const panel = inspectorPanel();
    const stops = [...panel.querySelectorAll<HTMLElement>("button, [role=tab], [tabindex='0']")];
    const first = within(panel).getByRole("separator", { name: "Resize inspector" });
    const close = within(panel).getByRole("button", { name: "Close inspector" });
    const last = stops[stops.length - 1]!;

    // Shift+Tab on the heading (the first stop is the resize handle, which a
    // phone hides) wraps to the last stop. Tab on the last stop wraps to the first.
    within(panel).getByRole("heading", { level: 2 }).focus();
    fireEvent.keyDown(document.activeElement!, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect([first, close]).toContain(document.activeElement);
  });

  it("leaves the thread live beside a docked or overlay panel", () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation(() => ({
        matches: false,
        addEventListener: () => {},
        removeEventListener: () => {},
      })),
    );
    const { container } = renderTurn([tool()]);
    openInspector(/^Queried Board/, "Inspect Queried Board");
    expect(container.querySelector("section.sourcecado-activity")).not.toHaveAttribute("inert");
  });

  it("shows no link for an address with a password or a script scheme", () => {
    const items: InspectorTarget[] = [
      {
        kind: "source",
        id: "creds",
        title: "With credentials",
        status: "success",
        externalUrl: "https://user:secret@creds.example.com/x",
      },
      { kind: "source", id: "script", title: "Script", status: "success", externalUrl: "javascript:alert(1)" },
      { kind: "source", id: "plain", title: "Plain", status: "success", externalUrl: "https://example.com/ok" },
    ];
    render(
      <InspectorProvider threadId="thread-urls">
        <KindHarness items={items} />
      </InspectorProvider>,
    );
    // The password must not show as text either.
    for (const id of ["creds", "script"]) {
      fireEvent.click(screen.getByRole("button", { name: `Open ${id}` }));
      expect(within(inspectorPanel()).queryByRole("link")).toBeNull();
      expect(inspectorPanel()).toHaveTextContent("External URL unavailable");
      expect(inspectorPanel()).not.toHaveTextContent("secret");
    }
    fireEvent.click(screen.getByRole("button", { name: "Open plain" }));
    expect(within(inspectorPanel()).getByRole("link", { name: "Open externally" })).toHaveAttribute(
      "href",
      "https://example.com/ok",
    );
  });
});

describe("Inspector with a failed step's Retry and overflow menu", () => {
  const failed = (): ToolCallMessagePart =>
    tool({
      toolCallId: "call-fail",
      toolName: "drive_search",
      args: { query: "Codeology" },
      result: { error: "PRIVATE_DETAIL" },
      isError: true,
      providerMetadata: {
        sourcecado: {
          failure: {
            class: "timeout_network",
            connector_id: "drive",
            source: "Google Drive",
            retry_safe: true,
            idempotent: true,
            summary: "Google Drive could not be reached. Retry is safe.",
            repair_route: null,
            detail: "PRIVATE_DETAIL",
            call_id: "call-fail",
            run_id: "run-1",
            session_id: "thread-1",
            state: "failed",
          },
          recovery: null,
        },
      },
    });

  it("keeps Inspect, Retry, the overflow menu and Escape working together", async () => {
    const onAction = vi.fn();
    render(
      <RecoveryProvider onAction={onAction}>
        <InspectorProvider threadId="thread-1">
          <ActivityGroup tools={[failed()]} messageState="complete" />
          <Inspector />
        </InspectorProvider>
      </RecoveryProvider>,
    );

    // The failed step opens by itself. Inspect opens the panel.
    const inspect = screen.getByRole("button", { name: "Inspect Searched Drive" });
    fireEvent.click(inspect);
    expect(within(inspectorPanel()).getByRole("alert")).toHaveTextContent(
      "Google Drive could not be reached.",
    );
    expect(inspectorPanel()).not.toHaveTextContent("PRIVATE_DETAIL");

    // Retry sends one command and does not close the panel.
    fireEvent.click(screen.getByRole("button", { name: /^Retry failed step/ }));
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith("retry", expect.objectContaining({ call_id: "call-fail" }));
    expect(inspectorPanel()).toBeInTheDocument();

    // The first Escape closes the open menu. The second closes the panel.
    const more = screen.getByRole("button", { name: /^More recovery options for/ });
    fireEvent.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(document.activeElement ?? more, { key: "Escape" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    expect(inspectorPanel()).toBeInTheDocument();

    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("complementary", { name: "Inspector" })).not.toBeInTheDocument();
    await waitFor(() => expect(inspect).toHaveFocus());
  });
});

describe("Inspector shows a result card read only", () => {
  const apollo = tool({
    toolCallId: "call-apollo",
    toolName: "apollo_search_people",
    args: { organizationName: "Apollo", personTitles: ["CEO"] },
    result: {
      people: [
        { apolloId: "p1", firstName: "Tim", lastNameObfuscated: "Zh***g", title: "CEO", organizationName: "Apollo.io", hasEmail: true, directPhoneStatus: null },
        { apolloId: "p2", firstName: "Maya", lastNameObfuscated: null, title: "Founder", organizationName: "Apollo.io", hasEmail: false, directPhoneStatus: null },
      ],
    },
  });
  const empty = tool({
    toolCallId: "call-empty",
    toolName: "apollo_search_people",
    args: { organizationName: "Nowhere" },
    result: { people: [] },
  });

  function renderThread(tools: ToolCallMessagePart[]) {
    return render(
      <InspectorProvider threadId="thread-readonly">
        <ActivityGroup tools={tools} messageState="complete" answerStarted />
        <AnswerResults tools={tools} messageState="complete" />
        <Inspector />
      </InspectorProvider>,
    );
  }

  it("has no select boxes and no curate, review or keep controls in the panel", () => {
    renderThread([apollo]);
    const threadCard = screen.getByRole("region", { name: "Apollo shortlist" });
    // The thread card is the one place to act.
    expect(within(threadCard).getAllByRole("checkbox")).toHaveLength(2);
    expect(within(threadCard).getByRole("textbox")).toBeInTheDocument();
    expect(within(threadCard).getByRole("button", { name: /^Review 0 selected/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Searched Apollo/ }));
    fireEvent.click(screen.getByRole("button", { name: "Inspect Searched Apollo" }));

    const panel = inspectorPanel();
    const copy = within(panel).getByRole("region", { name: "Apollo shortlist" });
    expect(within(copy).getByText("Tim")).toBeInTheDocument();
    expect(within(copy).queryByRole("checkbox")).toBeNull();
    expect(within(copy).queryByRole("textbox")).toBeNull();
    expect(within(copy).queryByRole("button", { name: /Review \d+ selected|Keep|Create chat|Open chat/ })).toBeNull();
    expect(copy.querySelector(".sourcecado-card-curate")).toBeNull();
    // Reading controls stay: the candidate and the credit note open items.
    expect(within(copy).getByRole("button", { name: "Inspect candidate Tim" })).toBeInTheDocument();
    expect(within(copy).getByRole("button", { name: "Review Apollo credit use" })).toBeInTheDocument();
    expect(panel).not.toHaveTextContent("Zh***g");
    // The thread card still has everything.
    expect(within(threadCard).getAllByRole("checkbox")).toHaveLength(2);
  });

  it("does not offer to edit the search from an empty card in the panel", () => {
    renderThread([empty]);
    const threadCard = screen.getByRole("region", { name: "Apollo shortlist" });
    expect(within(threadCard).getByRole("button", { name: "Adjust criteria" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Searched Apollo/ }));
    fireEvent.click(screen.getByRole("button", { name: "Inspect Searched Apollo" }));
    expect(
      within(inspectorPanel()).queryByRole("button", { name: "Adjust criteria" }),
    ).toBeNull();
    expect(within(threadCard).getByRole("button", { name: "Adjust criteria" })).toBeInTheDocument();
  });
});

describe("Inspector formatting", () => {
  it("formats a clock time and a duration for people", () => {
    expect(formatClock(at(14, 2, 11))).toBe("14:02:11");
    expect(formatClock(at(0, 5, 9))).toBe("00:05:09");
    expect(formatDuration(0)).toBe("<0.1s");
    expect(formatDuration(800)).toBe("0.8s");
    expect(formatDuration(12_400)).toBe("12s");
    expect(formatDuration(65_000)).toBe("1m 05s");
  });

  it("says only what was recorded", () => {
    const startedAt = at(14, 2, 11);
    expect(formatTiming({ startedAt, completedAt: startedAt + 800 }, false, 0)).toBe(
      "Started 14:02:11 · 0.8s",
    );
    expect(formatTiming({ startedAt }, true, startedAt + 3_000)).toBe("Started 14:02:11 · 3.0s");
    expect(formatTiming({ startedAt }, false, startedAt + 3_000)).toBe("Started 14:02:11");
    expect(formatTiming(undefined, false, 0)).toBeNull();
  });

  it("masks user info in an address and leaves everything else alone", () => {
    expect(maskUrlCredentials("https://u:SECRETPW123@host.example/p")).toBe("https://***@host.example/p");
    expect(maskUrlCredentials("HTTP://u@host.example")).toBe("HTTP://***@host.example");
    expect(maskUrlCredentials("https://host.example/a@b?x=y@z#f@g")).toBe("https://host.example/a@b?x=y@z#f@g");
    expect(maskUrlCredentials("mail me at a@b.com")).toBe("mail me at a@b.com");
    expect(maskUrlCredentials(["https://a:b@h/x", { k: "https://c@h/y", n: 3, z: null }])).toEqual([
      "https://***@h/x",
      { k: "https://***@h/y", n: 3, z: null },
    ]);
  });

  it("turns argument keys into words", () => {
    expect(humanizeKey("personTitles")).toBe("Person titles");
    expect(humanizeKey("organization_name")).toBe("Organization name");
  });
});
