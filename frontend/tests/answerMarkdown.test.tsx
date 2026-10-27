import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnswerMarkdown } from "../src/chat/AnswerMarkdown";
import { FORMATTED_ANSWER } from "./answerFixture";

const writeText = vi.fn();

function setClipboard(value: unknown) {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value,
  });
}

function codeBlocks(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLElement>(".sourcecado-code"));
}

describe("AnswerMarkdown formatting", () => {
  beforeEach(() => {
    writeText.mockReset();
    writeText.mockResolvedValue(undefined);
    setClipboard({ writeText });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders headings, nested lists, a quote, a wide table and code as one answer", () => {
    const { container } = render(<AnswerMarkdown text={FORMATTED_ANSWER} />);

    expect(
      screen.getAllByRole("heading").map((heading) => heading.tagName),
    ).toEqual(["H1", "H2", "H3", "H3"]);
    // Three levels of bullets and a numbered list inside a numbered list.
    expect(container.querySelector("ul ul ul")).not.toBeNull();
    expect(container.querySelector("ol ol")).not.toBeNull();
    expect(container.querySelector("blockquote")).toHaveTextContent(
      "Happy to talk after the demo day.",
    );
    const table = screen.getByRole("region", { name: "Table" });
    expect(table).toHaveAttribute("tabindex", "0");
    expect(within(table).getAllByRole("columnheader")).toHaveLength(8);
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(screen.getByText("fit_score").tagName).toBe("CODE");
    expect(codeBlocks(container)).toHaveLength(2);
  });

  it("shows the language and an icon-only Copy button on each fenced block", () => {
    const { container } = render(<AnswerMarkdown text={FORMATTED_ANSWER} />);

    const [python, json] = codeBlocks(container);
    expect(within(python).getByText("python")).toBeInTheDocument();
    expect(within(json).getByText("json")).toBeInTheDocument();
    for (const block of [python, json]) {
      const copy = within(block).getByRole("button", { name: "Copy code" });
      expect(copy).toHaveTextContent("");
      expect(copy.querySelector("svg")).not.toBeNull();
    }
  });

  it("colors tokens with classes and keeps the code text exact", () => {
    const code = 'def rank(items):\n    return "ok"  # done\n';
    const { container } = render(
      <AnswerMarkdown text={"```python\n" + code + "```"} />,
    );

    const block = container.querySelector("pre code") as HTMLElement;
    expect(block.querySelector(".hljs-keyword")).toHaveTextContent("def");
    expect(block.querySelector(".hljs-string")).toHaveTextContent('"ok"');
    expect(block.querySelector(".hljs-comment")).toHaveTextContent("# done");
    expect(block.textContent).toBe(code.replace(/\n$/, ""));
  });

  it("copies exactly the code, leaves focus alone and never scrolls", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    const scrollTo = vi.spyOn(HTMLElement.prototype, "scrollTo");
    const { container } = render(
      <>
        <input aria-label="Elsewhere" />
        <AnswerMarkdown text={FORMATTED_ANSWER} />
      </>,
    );
    const [python] = codeBlocks(container);
    const copy = within(python).getByRole("button", { name: "Copy code" });
    copy.focus();

    fireEvent.click(copy);

    expect(await within(python).findByText("Copied")).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledTimes(1);
    const copied = writeText.mock.calls[0][0] as string;
    expect(copied.startsWith("def rank(people, weights):")).toBe(true);
    expect(copied.endsWith("return sorted(scored, reverse=True)")).toBe(true);
    expect(copied).not.toContain("```");
    expect(copied).not.toContain("python");
    expect(document.activeElement).toBe(copy);
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("says when the clipboard refuses, then returns to idle", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error("denied")) });
    render(<AnswerMarkdown text={"```bash\nls -la\n```"} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    });
    expect(screen.getByRole("status")).toHaveTextContent("Copy failed");

    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByRole("status")).toHaveTextContent("");
  });

  it("reports failure in words, not only color, when the clipboard API is missing", async () => {
    setClipboard(undefined);
    render(<AnswerMarkdown text={"```bash\nls -la\n```"} />);
    const copy = screen.getByRole("button", { name: "Copy code" });
    expect(copy).toHaveAttribute("title", "Copy code");

    await act(async () => {
      fireEvent.click(copy);
    });
    // The visible status carries the words. Color only adds to them.
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Copy failed");
    expect(status).not.toHaveClass("sr-only");
    expect(copy).toHaveAttribute("title", "Copy failed");
  });

  it("labels a block with no language as text and does not color it", () => {
    const { container } = render(
      <AnswerMarkdown text={"```\nplain def return\n```\n\n```nosuchlang\ndef x\n```"} />,
    );

    const [plain, unknown] = codeBlocks(container);
    expect(within(plain).getByText("text")).toBeInTheDocument();
    expect(plain.querySelector("[class*='hljs-']")).toBeNull();
    expect(within(unknown).getByText("nosuchlang")).toBeInTheDocument();
    expect(unknown.querySelector("[class*='hljs-']")).toBeNull();
    expect(plain.querySelector("pre")).toHaveTextContent("plain def return");
  });

  it("never turns raw HTML in an answer into elements", () => {
    const hostile = [
      "<script>window.__pwned = 1</script>",
      '<img src="x" onerror="window.__pwned = 2">',
      "<iframe src=\"https://example.com\"></iframe>",
      '<a href="javascript:window.__pwned=3">raw link</a>',
      "[markdown link](javascript:window.__pwned=4)",
      "```html\n<script>window.__pwned = 5</script>\n```",
    ].join("\n\n");
    const { container } = render(<AnswerMarkdown text={hostile} />);

    expect(
      container.querySelectorAll("script, img, iframe, object, embed"),
    ).toHaveLength(0);
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(container.querySelectorAll("a")).toHaveLength(0);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    expect(container).toHaveTextContent("<script>window.__pwned = 1</script>");
  });

  it("shows a javascript: or data: link as plain text, not as an anchor", () => {
    const { container } = render(
      <AnswerMarkdown
        text={
          "[js link](javascript:window.__pwned=1) and [data link](data:text/html,x) and [empty]() and [ok](https://acme.example)"
        }
      />,
    );

    // Only the safe link stays an anchor. The unsafe ones keep their words.
    const anchors = Array.from(container.querySelectorAll("a"));
    expect(anchors.map((a) => a.getAttribute("href"))).toEqual([
      "https://acme.example",
    ]);
    expect(container.querySelector("p")).toHaveTextContent(
      "js link and data link and empty and ok",
    );
  });

  it("colors a block that grows, and keeps the text exact at each step", () => {
    const lines = (count: number) =>
      Array.from({ length: count }, (_, i) => `def fn_${i}(): return ${i}`).join("\n");
    const fence = (code: string) => "```python\n" + code + "\n```";
    const { container, rerender } = render(<AnswerMarkdown text={fence(lines(10))} />);
    const code = container.querySelector("pre code") as HTMLElement;

    rerender(<AnswerMarkdown text={fence(lines(30))} />);

    expect(container.querySelector("pre code")).toBe(code);
    expect(code.textContent).toBe(lines(30));
    const defs = Array.from(code.querySelectorAll(".hljs-keyword")).filter(
      (keyword) => keyword.textContent === "def",
    );
    expect(defs).toHaveLength(30);
  });

  it("shows a very long block as plain text and still copies all of it", async () => {
    const code = Array.from({ length: 7000 }, (_, i) => `x_${i} = ${i}`).join("\n");
    expect(code.length).toBeGreaterThan(40_000);
    const { container } = render(<AnswerMarkdown text={"```python\n" + code + "\n```"} />);

    const block = codeBlocks(container)[0];
    expect(block.querySelector("[class*='hljs-']")).toBeNull();
    expect(block.querySelector("pre code")?.textContent).toBe(code);
    await act(async () => {
      fireEvent.click(within(block).getByRole("button", { name: "Copy code" }));
    });
    expect(writeText).toHaveBeenCalledWith(code);
  });

  it("opens links in a new window without leaking the opener", () => {
    render(<AnswerMarkdown text="[Acme](https://acme.example)" />);

    const link = screen.getByRole("link", { name: "Acme" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer noopener");
    expect(link).not.toHaveAttribute("node");
  });

  it("keeps the layout and the code controls intact at every point of a stream", () => {
    const { container, rerender } = render(<AnswerMarkdown text="" />);
    let firstCopy: HTMLElement | null = null;

    for (let end = 1; end <= FORMATTED_ANSWER.length; end += 24) {
      rerender(<AnswerMarkdown text={FORMATTED_ANSWER.slice(0, end)} />);

      // Plain DOM queries here: role queries cost too much across ~250 renders.
      for (const block of codeBlocks(container)) {
        const buttons = block.querySelectorAll("button");
        expect(buttons).toHaveLength(1);
        expect(buttons[0]).toHaveAttribute("aria-label", "Copy code");
        expect(block.querySelectorAll(".sourcecado-code-lang")).toHaveLength(1);
      }
      // Every pre sits inside a toolbar block, and every table scrolls alone.
      for (const pre of Array.from(container.querySelectorAll("pre"))) {
        expect(pre.closest(".sourcecado-code")).not.toBeNull();
      }
      for (const table of Array.from(container.querySelectorAll("table"))) {
        expect(table.parentElement).toHaveClass("sourcecado-table-scroll");
      }
      const copy = container.querySelector(".sourcecado-code-copy");
      if (copy && !firstCopy) firstCopy = copy as HTMLElement;
      // The control that appeared first is the same node later: it is never
      // remounted by a new chunk, so focus and the Copied state survive.
      if (firstCopy) {
        expect(container.querySelector(".sourcecado-code-copy")).toBe(firstCopy);
      }
    }
    expect(firstCopy).not.toBeNull();
    expect(codeBlocks(container)).toHaveLength(2);
  });

  it("keeps an unclosed fence and an unfinished table inside their blocks", () => {
    const { container } = render(
      <AnswerMarkdown
        text={"| a | b |\n| --- | --- |\n| one | tw\n\n```python\ndef half(:"}
      />,
    );

    expect(container.querySelectorAll(".sourcecado-table-scroll")).toHaveLength(1);
    const [block] = codeBlocks(container);
    expect(within(block).getByRole("button", { name: "Copy code" })).toBeVisible();
    expect(block.querySelector("pre")).toHaveTextContent("def half(:");
  });

  it("keeps a text selection across finished paragraphs while the last one streams", () => {
    const first = "First paragraph with [a link](https://acme.example) inside.";
    const second = "Second paragraph.";
    const { container, rerender } = render(
      <AnswerMarkdown text={`${first}\n\n${second}\n\nThe tail starts`} />,
    );
    const [p1, p2] = Array.from(container.querySelectorAll("p"));
    const link = container.querySelector("a");
    const range = document.createRange();
    range.setStart(p1.firstChild as Node, 0);
    range.setEnd(p2.firstChild as Node, "Second paragraph.".length);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    const selected = selection.toString();
    expect(selected).toContain("First paragraph");
    expect(selected).toContain("Second paragraph.");

    rerender(
      <AnswerMarkdown
        text={`${first}\n\n${second}\n\nThe tail starts and keeps going.\n\n- A new list`}
      />,
    );

    // The finished paragraphs and the link keep the same DOM nodes. A components
    // map rebuilt on each chunk would replace them and drop the selection.
    const [after1, after2] = Array.from(container.querySelectorAll("p"));
    expect(after1).toBe(p1);
    expect(after2).toBe(p2);
    expect(container.querySelector("a")).toBe(link);
    expect(window.getSelection()!.toString()).toBe(selected);
  });
});
