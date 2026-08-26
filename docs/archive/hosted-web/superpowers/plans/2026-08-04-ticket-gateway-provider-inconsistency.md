# Ticket — The gateway accepts `openai` for streaming but rejects it for `callModel`

Date: 2026-08-04
Found by: starting the dev server with `SOURCECADO_GENERATION_PROVIDER=openai`
Est: 0.25

## Problem

One provider name, two different answers depending on which code path reaches it.

| Path | `openai` | Location |
|---|---|---|
| `pickAdapter` — agent loop, chat streaming | **accepted** | `src/lib/model-gateway.ts:587` |
| `assertSupportedGenerationProvider` — `callModel` | **rejected** | `src/lib/model-gateway.ts:327` |

```ts
// :584-587  — openai is a first-class streaming provider
function pickAdapter(providerName: string): LlmAdapter {
  if (providerName === "anthropic") return anthropicAdapter;
  if (providerName === "deepseek") return createOpenAiCompatAdapter("deepseek");
  if (providerName === "openai") return createOpenAiCompatAdapter("openai");

// :326-330  — ...and an unsupported one here
function assertSupportedGenerationProvider(providerName: string): asserts providerName is "anthropic" | "deepseek" {
  if (providerName !== "anthropic" && providerName !== "deepseek") {
    throw new ModelGatewayError("config_error", `Unsupported generation provider: ${providerName}. ...`);
```

Set `SOURCECADO_GENERATION_PROVIDER=openai` and chat works perfectly — full agent
runs, tool chaining, streamed answers. Then memory ingest throws
`config_error: Unsupported generation provider: openai`.

## Why it is worse than a missing feature

The failure is **far from its cause**. Chat is the thing you exercise first and
it works, so the configuration reads as valid. The error only appears later, in
an unrelated feature, with a message that says the provider is unsupported —
directly contradicting the chat session still streaming in the next tab.

Blast radius is small only by accident: `callModel` has just two callers —
`src/lib/memory/embed.ts:17` (which resolves to `openai` for `embed` kinds
regardless, so it is unaffected) and `src/extractors/llm.ts:197` (memory
ingest/extraction, which breaks).

## Fix — decide which, do not do both

**(a) Support openai in `callModel`.** Add an `openaiClient()` alongside
`deepseekClient()` and widen the assertion. openai supports
`response_format: json_object` the same way the deepseek path already uses, so
this is close to a copy of the existing branch. Makes the two paths agree by
raising the weaker one.

**(b) Reject openai for generation everywhere.** Remove the `openai` branch from
`pickAdapter` so the provider is refused up front, at startup, with a clear
message — rather than half-working. Makes the two paths agree by lowering the
stronger one.

(a) is preferable on the evidence: openai generation demonstrably works end to
end through the agent loop, and openai is the only provider whose key is
currently valid without billing issues. But whichever is chosen, the invariant is
**one provider list, one answer.**

## Notes for implementation

- `resolveProviderName` (`:130`) defaults to `"deepseek"` when
  `SOURCECADO_GENERATION_PROVIDER` is unset, and forces `"openai"` for `embed` /
  `embed_many`. Embeddings are a separate axis from generation and must stay
  that way — do not collapse them while unifying the generation list.
- Whichever fix is chosen, add a test that asserts the streaming and
  `callModel` provider lists are the *same set*. The bug exists because two
  lists were maintained independently; only a test comparing them prevents the
  next drift.

## Done when

- A provider accepted by one path is accepted by both, or rejected by both.
- A test fails if the two lists diverge again.
- The rejection message names where to set the value, and appears at
  configuration time rather than on first structured call.
