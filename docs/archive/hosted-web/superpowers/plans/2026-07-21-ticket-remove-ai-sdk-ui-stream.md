# Ticket: Remove the last `ai` dependency — migrate ui-message-stream.ts off the AI SDK

Date: 2026-07-21 · Status: TICKETED (spun out of R8). Blocked by: nothing.
Priority: cleanup — finishes the sprint's "full AI SDK rip-out" intent.

## Problem

R8 removed `@ai-sdk/anthropic|deepseek|openai` from `package.json`, but `ai`
remains because `src/lib/ui-message-stream.ts` (R5-owned SSE transport) imports
`createUIMessageStream` + `createUIMessageStreamResponse` from `"ai"`. It is the
only remaining `from "ai"` importer in `src/` (enforced by
`tests/model-boundary.test.ts`, which allows `ai` there).

## Fix shape

Rewrite `ui-message-stream.ts` to emit the SSE `data:` frames + response
(`text/event-stream`, the UI-message chunk shapes the `/api/agent/stream` route
+ `ChatClient` already consume) without the two `ai` helpers — they are thin
wrappers over a `ReadableStream` + `Response`. Then:
- Remove `ai` from `package.json` (`npm uninstall ai`).
- Update `tests/package-deps.test.ts`: `ai` should now be ABSENT — flip the
  "still depends on `ai`" assertion to assert its absence.
- Update `tests/model-boundary.test.ts`: drop `ui-message-stream.ts` from the
  allowed-`ai`-importers set (nothing should import `ai` anymore).
- Verify the streaming chat still works end-to-end (R5's live-probe shape:
  token-by-token deltas + tool-pending parts over the wire).

## Why deferred from R8

R8's ownership was `model-gateway.ts` internals; `ui-message-stream.ts` is
R5/streaming-transport territory, and hand-rolling the SSE framing is a
distinct, testable change better done as its own slice than bolted onto the
model-gateway migration.
