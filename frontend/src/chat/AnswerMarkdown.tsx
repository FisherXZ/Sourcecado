import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { createLowlight } from "lowlight";
import type { Element, RootContent } from "hast";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import shell from "highlight.js/lib/languages/shell";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

// Raw HTML in an answer stays inert: react-markdown renders it as text because
// no rehype-raw plugin is installed. Do not add one.
const lowlight = createLowlight({
  bash,
  css,
  diff,
  javascript,
  json,
  markdown,
  python,
  shell,
  sql,
  typescript,
  xml,
  yaml,
});

const COPIED_MS = 1800;

// The highlighter returns a tree, so the code renders as React elements and no
// HTML string is ever injected. Only color classes are added: token spans never
// change size or weight, so partial code does not shift the layout.
function renderToken(node: RootContent, key: number): ReactNode {
  if (node.type === "text") return node.value;
  if (node.type !== "element") return null;
  const className = Array.isArray(node.properties.className)
    ? node.properties.className.join(" ")
    : undefined;
  return (
    <span key={key} className={className}>
      {node.children.map(renderToken)}
    </span>
  );
}

function highlight(text: string, language: string | null): ReactNode {
  if (!language || !lowlight.registered(language)) return text;
  try {
    return lowlight.highlight(language, text).children.map(renderToken);
  } catch {
    return text;
  }
}

function CopyIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" />
      <path d="M10.5 3.5v-.5A1.5 1.5 0 0 0 9 1.5H3A1.5 1.5 0 0 0 1.5 3v6A1.5 1.5 0 0 0 3 10.5h.5" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m3 8.5 3.2 3.2L13 4.8" />
    </svg>
  );
}

type CopyState = "idle" | "copied" | "failed";

function CodeBlock({
  text,
  language,
}: {
  readonly text: string;
  readonly language: string | null;
}) {
  const [copy, setCopy] = useState<CopyState>("idle");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const content = useMemo(() => highlight(text, language), [text, language]);
  const label = language ?? "text";

  // Copy uses the async clipboard API only. A hidden-textarea fallback would
  // move focus and scroll the thread.
  const copyCode = async () => {
    let next: CopyState = "copied";
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      next = "failed";
    }
    setCopy(next);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopy("idle"), COPIED_MS);
  };

  return (
    <div className="sourcecado-code" role="group" aria-label={`${label} code`}>
      <div className="sourcecado-code-head">
        <span className="sourcecado-code-lang">{label}</span>
        <button
          type="button"
          className="sourcecado-code-copy"
          aria-label="Copy code"
          data-state={copy}
          onClick={() => void copyCode()}
        >
          {copy === "copied" ? <CheckIcon /> : <CopyIcon />}
        </button>
        <span className="sr-only" role="status">
          {copy === "copied" ? "Code copied" : copy === "failed" ? "Copy failed" : ""}
        </span>
      </div>
      <pre tabIndex={0}>
        <code className={language ? `hljs language-${language}` : "hljs"}>
          {content}
        </code>
      </pre>
    </div>
  );
}

function codeOf(
  node: Element | undefined,
): { text: string; language: string | null } | null {
  const code = node?.children.find(
    (child): child is Element =>
      child.type === "element" && child.tagName === "code",
  );
  if (!code) return null;
  const text = code.children
    .map((child) => (child.type === "text" ? child.value : ""))
    .join("")
    .replace(/\n$/, "");
  const classes = Array.isArray(code.properties.className)
    ? code.properties.className.map(String)
    : [];
  const language =
    classes.find((name) => name.startsWith("language-"))?.slice(9) ?? null;
  return { text, language };
}

// Declared once at module level. A components map built inside a render would
// give every element type a new identity on each streamed chunk, remount the
// DOM, and drop the operator's text selection.
const components: Components = {
  a: ({ node: _node, children, ...props }) => (
    <a {...props} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
  table: ({ node: _node, children, ...props }) => (
    <div
      className="sourcecado-table-scroll"
      role="region"
      aria-label="Table"
      tabIndex={0}
    >
      <table {...props}>{children}</table>
    </div>
  ),
  pre: ({ node, children, ...props }) => {
    const block = codeOf(node);
    if (!block) return <pre {...props}>{children}</pre>;
    return <CodeBlock text={block.text} language={block.language} />;
  },
};

export function AnswerMarkdown({ text }: { readonly text: string }) {
  return (
    <div className="sourcecado-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
