# R8 — AI SDK Rip-Out Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `callModel`'s `generate_text`/`generate_object`/`embed`/`embed_many`
internals in `src/lib/model-gateway.ts` off the Vercel AI SDK (`ai`,
`@ai-sdk/anthropic`, `@ai-sdk/deepseek`, `@ai-sdk/openai`) onto the raw
`@anthropic-ai/sdk` and `openai` packages, with zero change to `callModel`'s
external contract (`CallModelInput`/`CallModelResult`/`ModelGatewayProvider`
test seam). Then remove the **`@ai-sdk/*`** packages from `package.json` — gated
on a repo-wide check that nothing else still imports them.

## Reconciliation (2026-07-21 — Task 5 scope narrowed vs the 2026-07-14 draft)

Grounded against current `main` (R5/R6/R7 all merged), decided with Fisher:
- **`ai` STAYS this slice.** `src/lib/ui-message-stream.ts` (R5-owned, merged)
  deliberately imports `createUIMessageStream`/`createUIMessageStreamResponse`
  from `"ai"` as the SSE transport, and `tests/model-boundary.test.ts` allows
  it there. So R8 removes only `@ai-sdk/anthropic|deepseek|openai`; fully
  removing `ai` requires rewriting the SSE transport (R5 domain) — filed as
  `docs/superpowers/plans/2026-07-21-ticket-remove-ai-sdk-ui-stream.md`.
- **The Task 5 guard grep is tightened** to match real `import ... from "ai"` /
  `@ai-sdk` *statements*, not comment text — `src/lib/llm/anthropic.ts` only
  mentions `@ai-sdk/anthropic` in a comment (it imports the raw `@anthropic-ai/sdk`),
  and the loose grep spuriously trips the plan's "stop and investigate" branch.
- Task 5's original "stop short because R5 hasn't landed" branch is therefore
  obsolete — R5 landed and keeps `ai` on purpose. Task 5 below is rewritten
  accordingly; Tasks 1–4 and 6 are unchanged.

**Depends on:** R1 (Provider adapter layer) for the `@anthropic-ai/sdk` +
`openai` packages being added as direct dependencies — this plan's Task 1
installs them itself if R1 hasn't landed yet, so it does not hard-block on
R1's PR merging first. Per the spec's cut order and dependency notes, R8 is
"last, independent" of R2–R7 (the agent-loop/tool-orchestrator/streaming
work) — this plan does not touch `harness.ts`, `agent-loop.ts`,
`tools/orchestrator.ts`, or any chat/streaming route. **Soft dependency on
R5:** `src/lib/ui-message-stream.ts` (R5-owned) is the only other file in the
repo that imports from `"ai"`; Task 5 (removing `ai`/`@ai-sdk/*` from
`package.json`) can only complete once that import is gone. If R5 hasn't
landed yet when this plan executes, Task 5 stops short of editing
`package.json` and records the gap — see Task 5's acceptance criteria.

## Context (read this before starting)

- Source spec: `docs/superpowers/specs/2026-07-14-runtime-solidification-sprint-spec.md`
  (R8 section, "Decisions locked," Acceptance Criteria #9).
- Binding contracts: `docs/superpowers/plans/2026-07-14-r-contracts-brief.md`
  §7 (file ownership table) — this plan's scope is exactly `src/lib/model-gateway.ts`,
  `src/lib/memory/embed.ts`, `src/extractors/llm.ts`, `package.json`. The
  brief's §1–§6 (LLM contract, `streamAgentTurn`, the loop, orchestrator,
  context assembly, chat sessions) are **R1–R6 territory and out of scope
  here** — this plan does not touch `src/lib/llm/`, `agent-loop.ts`,
  `tools/orchestrator.ts`, `context.ts`, or chat session files.
- Current repo state (verified 2026-07-14): `src/lib/model-gateway.ts` imports
  `createAnthropic` from `@ai-sdk/anthropic`, `deepseek` from `@ai-sdk/deepseek`,
  `openai` from `@ai-sdk/openai`, and `embed`/`embedMany`/`generateObject`/`generateText`
  plus the `LanguageModel` type from `ai`. `executeDefaultProvider()` branches
  on `input.kind` and calls those four AI SDK functions; `generationModel()`
  builds an AI-SDK `LanguageModel` for `anthropic`/`deepseek` (no `openai`
  generation provider is supported today — embeddings always use OpenAI,
  independent of the generation provider).
  `src/extractors/llm.ts` and `src/lib/memory/embed.ts` **only call
  `callModel(...)`** — neither imports `ai` or `@ai-sdk/*` directly, and their
  existing test suites (`tests/extractors.test.ts`, `tests/memory-extract.test.ts`,
  `tests/memory-embed.test.ts`) inject a `provider`/`config.provider` function
  seam that bypasses `executeDefaultProvider()` entirely. That means neither
  file needs a code change for this migration — Task 4 verifies that by
  running their suites unchanged, it does not edit those files unless a
  regression surfaces.
  Existing tests that *do* exercise `executeDefaultProvider`'s config-resolution
  path without mocking the network (`tests/model-gateway-anthropic.test.ts`)
  must keep passing byte-for-byte: unknown provider → `config_error` matching
  `/bogus-provider/`; anthropic + missing `ANTHROPIC_API_KEY` → `missing_config`
  matching `/ANTHROPIC_API_KEY/`.
- `resolveAnthropicBaseUrl()` (exported, tested in `tests/anthropic-base-url.test.ts`)
  normalizes a base URL to end in `/v1` — that convention matches
  `@ai-sdk/anthropic`'s expectation, **not** the raw `@anthropic-ai/sdk`'s.
  The raw SDK's own default base URL is the bare host
  (`https://api.anthropic.com`, no `/v1` — it appends the versioned path
  itself). Do not change `resolveAnthropicBaseUrl()`'s behavior or its test —
  add a second small pure function that strips the `/v1` suffix back off
  before constructing the raw `Anthropic` client (Task 1).
- Model support is unchanged by this migration: generation only supports
  `"anthropic"` and `"deepseek"` (an unsupported provider throws
  `config_error`, exactly as today); there is no `"openai"` generation
  provider to add — that would be new scope, not requested. Embeddings always
  use OpenAI via `OPENAI_API_KEY`, independent of the generation provider —
  also unchanged.
- DeepSeek's OpenAI-compatible endpoint is at `https://api.deepseek.com`
  (verified against `@ai-sdk/deepseek`'s own default in
  `node_modules/@ai-sdk/deepseek/dist/index.js`) — the raw `openai` package's
  client posts `${baseURL}/chat/completions` without injecting a `/v1`
  segment itself, so pointing it at that same base URL reproduces today's
  DeepSeek behavior exactly.
- The configured generation model is `claude-sonnet-4-6` (`.env.example`),
  which is **not** on the model list that supports Anthropic's native
  `output_config.format` structured outputs — so `generate_object` on
  Anthropic must use tool-forcing (a single tool, `tool_choice: {type: "tool",
  name: ...}`, extract the tool's `input`), exactly as the spec's parenthetical
  says. This is a correctness requirement for the configured model, not a
  style preference.
- `z.toJSONSchema` (Zod v4) is already used elsewhere in the repo
  (`src/lib/harness.ts:206`, pre-R2) to convert a tool's Zod schema to JSON
  Schema for an API `tools:` field — same technique, reused here for
  `generate_object`'s schema. `model-gateway.ts` currently does `import type
  { z } from "zod"` (type-only); this plan changes it to a value import
  (`import { z } from "zod"`) since `z.toJSONSchema` is called at runtime.

## Judgment calls

- **R8 does not reuse R1's `src/lib/llm/anthropic.ts` / `openai-compat.ts`
  streaming-turn adapters.** Those are shaped for the tool-calling agent loop
  (`LlmTurnRequest`/`LlmStreamEvent`, no `tool_choice` concept in the R1
  contract) — routing a one-shot `generate_object` call through a forced
  single-tool streaming turn would require adding a `tool_choice` field to the
  R1 contract, which is out of scope for this slice and would be a silent
  contract change the brief says to avoid. Instead, `generate_text`/`generate_object`/`embed`/`embed_many`
  call the raw `@anthropic-ai/sdk` / `openai` clients directly inside
  `model-gateway.ts`, self-contained. This also keeps R8 genuinely
  independent of R1's loop-specific files landing first (only the two raw
  SDK *packages* are a prerequisite, not R1's new modules), matching the
  spec's "R8 (last, independent)" note.
- **Anthropic's raw SDK requires `max_tokens` on every request** (the AI SDK
  supplied a default implicitly — `@ai-sdk/anthropic`'s `maxOutputTokensForModel`
  table resolves **64000** for any `claude-sonnet-4-*` model, including the
  configured `claude-sonnet-4-6`, not a small flat number). No caller of
  `generate_text`/`generate_object` passes a token limit today, so this plan
  must reproduce that effective per-model ceiling rather than shrink it.
  DeepSeek is unaffected: `@ai-sdk/deepseek` already passes `max_tokens:
  undefined`, i.e. no explicit limit, so DeepSeek's own server-side default
  (4096 for `deepseek-chat`) already governs today — a flat `DEFAULT_MAX_TOKENS
  = 4096` for DeepSeek reproduces existing behavior exactly. Anthropic gets its
  own small per-model-family ceiling function, `anthropicMaxTokensForModel()`
  (`claude-sonnet-4-*` → 64000, fallback 4096), used by both
  `generateTextAnthropic` and `generateObjectAnthropic` instead of a shared
  flat constant. Neither is exposed on `CallModelInput` — no caller has ever
  needed to configure it, so adding a public knob here would be speculative
  flexibility beyond what's requested.
  Two live production callers of `callModel(..., kind: "generate_object")` are
  affected by this ceiling today (confirmed independent of R2/R5 landing
  first, since R8 is "last, independent"): `src/lib/harness.ts`'s `decide()`
  (the shipped Research Chat ReAct loop, task `agent_react_decide`) and
  `src/extractors/llm.ts`'s document-wide candidate extraction (task
  `extract_memory_candidates`). Task 1 must state explicitly that both keep
  their current effective ceiling (64000, via `anthropicMaxTokensForModel`)
  rather than being silently cut to 4096.
- **`ui-message-stream.ts` (R5-owned) is the only other AI-SDK importer in the
  repo.** Task 5 cannot delete/edit that file (not this slice's ownership per
  the contracts brief) and cannot remove `ai`/`@ai-sdk/*` from `package.json`
  while it still imports from `"ai"`. The task is written to detect this via
  a repo-wide grep and stop short (recording the gap) rather than either
  breaking the build or silently editing an out-of-scope file. This bullet is
  the durable record of that gap — see also Task 5's acceptance criteria.
- **Embedding usage/`generate_*` usage shapes are re-mapped to the exact
  shapes `normalizeUsage()` already special-cases**, so `normalizeUsage()`
  itself needs no changes: embeddings map to `{ tokens: n }` (matching the
  AI SDK's embedding-usage convention `normalizeUsage` already branches on),
  and text/object generation map to `{ inputTokens, outputTokens, totalTokens
  }` camelCase (matching the AI SDK's generation-usage convention
  `normalizeUsage` already branches on). This is the leanest way to reuse the
  existing, already-tested normalization logic unchanged.
- **`embeddings.create()`'s response `data` array is defensively re-sorted by
  `index`** before mapping to plain embedding arrays for `embed_many` — the
  OpenAI API returns entries in input order, but re-sorting is one line and
  removes any dependency on that ordering guarantee (a silent misordering
  here would corrupt every affected memory embedding).

## Tasks

### Task 1: Anthropic raw-SDK migration — `generate_text` + `generate_object`

**Build:** Install `@anthropic-ai/sdk` (idempotent — no-ops if R1 already
added it); add `toBareAnthropicHost()`; replace the Anthropic branch of
`executeDefaultProvider()`'s `generate_text` and `generate_object` cases with
raw-SDK calls (`generate_object` via a single forced tool call).

**Files:**
- Modify: `src/lib/model-gateway.ts`
- Modify: `tests/anthropic-base-url.test.ts` (add `toBareAnthropicHost` tests)
- Create: `tests/model-gateway-anthropic-provider.test.ts`
- Modify: `package.json`, `package-lock.json` (via `npm install`)

**Step 1: Install the raw Anthropic SDK.**

```bash
npm install @anthropic-ai/sdk
```

Verify: `grep '"@anthropic-ai/sdk"' package.json` shows a version under
`dependencies`.

**Step 2: Add `toBareAnthropicHost()` next to `resolveAnthropicBaseUrl()`** in
`src/lib/model-gateway.ts`:

```ts
// The raw @anthropic-ai/sdk client posts to `${baseURL}/v1/messages` itself —
// unlike @ai-sdk/anthropic, it wants the bare host, not a URL that already
// carries a version segment. resolveAnthropicBaseUrl() stays as-is (its
// tested contract is the /v1-suffixed form some callers configure); this
// strips that suffix back off right before constructing the raw client.
export function toBareAnthropicHost(versionedBaseUrl: string): string {
  return versionedBaseUrl.replace(/\/v\d+$/, "");
}
```

**Step 3: Add the failing tests** — append to `tests/anthropic-base-url.test.ts`:

```ts
import { resolveAnthropicBaseUrl, toBareAnthropicHost } from "@/lib/model-gateway";

describe("toBareAnthropicHost", () => {
  it("strips a trailing /v1 segment", () => {
    expect(toBareAnthropicHost("https://api.anthropic.com/v1")).toBe(
      "https://api.anthropic.com",
    );
  });

  it("strips a trailing /v2 (or other version) segment", () => {
    expect(toBareAnthropicHost("https://proxy.internal/anthropic/v2")).toBe(
      "https://proxy.internal/anthropic",
    );
  });

  it("is a no-op when there is no version segment", () => {
    expect(toBareAnthropicHost("https://api.anthropic.com")).toBe(
      "https://api.anthropic.com",
    );
  });

  it("composes with resolveAnthropicBaseUrl for the default case", () => {
    expect(toBareAnthropicHost(resolveAnthropicBaseUrl(undefined))).toBe(
      "https://api.anthropic.com",
    );
  });
});
```

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/anthropic-base-url.test.ts`
Expected: FAIL — `toBareAnthropicHost` is not exported yet (it is defined in
Step 2, so if you do Step 2 first this will already pass; either order is
fine, but write the test before relying on the implementation).

**Step 4: Rewrite the Anthropic generation path in `src/lib/model-gateway.ts`.**

Change the import block:

```ts
import { createHash } from "node:crypto";
import Anthropic from "@anthropic-ai/sdk";
import type postgres from "postgres";
import { z } from "zod"; // was: import type { z } from "zod" — z.toJSONSchema is a runtime call
import { failRunStep, finishRunStep, startRunStep } from "./ledger";
```

(Leave the `@ai-sdk/deepseek`, `@ai-sdk/openai`, and `ai` imports in place for
now — Tasks 2 and 3 remove them as those branches migrate. Do not remove
`generationModel()` yet either; Task 2 deletes it once both its branches are
gone.)

Add near `resolveAnthropicBaseUrl`:

```ts
// DeepSeek's own server-side default already governs when no explicit
// max_tokens is sent (verified: @ai-sdk/deepseek passes `max_tokens:
// undefined` today), so a flat constant reproduces existing DeepSeek
// behavior exactly. Anthropic does NOT get this constant — see
// anthropicMaxTokensForModel() below, which reproduces @ai-sdk/anthropic's
// per-model-family default instead of shrinking it.
const DEFAULT_MAX_TOKENS = 4096;

// Mirrors @ai-sdk/anthropic's maxOutputTokensForModel table (the raw SDK has
// no implicit default and requires max_tokens on every request). Without
// this, every claude-sonnet-4-* call — including the configured
// claude-sonnet-4-6 — would silently drop from an effective 64000-token
// ceiling to 4096, truncating both harness.ts's decide() and
// extractors/llm.ts's document-wide extraction.
function anthropicMaxTokensForModel(model: string): number {
  return /^claude-sonnet-4-/.test(model) ? 64000 : DEFAULT_MAX_TOKENS;
}

function anthropicClient(): Anthropic {
  requireEnv("ANTHROPIC_API_KEY");
  return new Anthropic({
    apiKey: process.env.ANTHROPIC_API_KEY,
    baseURL: toBareAnthropicHost(resolveAnthropicBaseUrl(process.env.ANTHROPIC_BASE_URL)),
  });
}

function assertSupportedGenerationProvider(
  providerName: string,
): asserts providerName is "anthropic" | "deepseek" {
  if (providerName !== "anthropic" && providerName !== "deepseek") {
    throw new ModelGatewayError(
      "config_error",
      `Unsupported generation provider: ${providerName}. Set SOURCECADO_GENERATION_PROVIDER to "anthropic" or "deepseek".`,
    );
  }
}

function toGenerationJsonSchema(schema: z.ZodType): unknown {
  return z.toJSONSchema(schema);
}

async function generateTextAnthropic(input: GenerateTextInput, model: string): Promise<ModelGatewayProviderResult> {
  const client = anthropicClient();
  const response = await client.messages.create({
    model,
    max_tokens: anthropicMaxTokensForModel(model),
    system: input.system,
    messages: [{ role: "user", content: input.prompt }],
  });
  const text = response.content
    .filter((block): block is Anthropic.TextBlock => block.type === "text")
    .map((block) => block.text)
    .join("");
  return {
    text,
    usage: {
      inputTokens: response.usage.input_tokens,
      outputTokens: response.usage.output_tokens,
      totalTokens: response.usage.input_tokens + response.usage.output_tokens,
    },
    rawResponse: response,
  };
}

async function generateObjectAnthropic(
  input: GenerateObjectInput,
  model: string,
): Promise<ModelGatewayProviderResult> {
  const client = anthropicClient();
  const toolName = "emit_object";
  const jsonSchema = toGenerationJsonSchema(input.schema);
  const response = await client.messages.create({
    model,
    max_tokens: anthropicMaxTokensForModel(model),
    system: input.system,
    messages: [{ role: "user", content: input.prompt }],
    tools: [
      {
        name: toolName,
        description: "Emit the structured result for this task. Always call this tool exactly once.",
        // z.toJSONSchema's output is a plain JSON Schema object; cast to the
        // SDK's InputSchema type. If the exact nested type path differs from
        // this SDK version, adjust the cast only (runtime shape is correct) —
        // confirm with `npm run build` / `tsc --noEmit`.
        input_schema: jsonSchema as Anthropic.Tool["input_schema"],
      },
    ],
    tool_choice: { type: "tool", name: toolName },
  });
  const toolUse = response.content.find(
    (block): block is Anthropic.ToolUseBlock => block.type === "tool_use" && block.name === toolName,
  );
  return {
    object: toolUse?.input,
    usage: {
      inputTokens: response.usage.input_tokens,
      outputTokens: response.usage.output_tokens,
      totalTokens: response.usage.input_tokens + response.usage.output_tokens,
    },
    rawResponse: response,
  };
}
```

In `executeDefaultProvider()`, replace the `generate_text` and
`generate_object` cases' bodies to branch on `providerName`, calling the new
Anthropic functions for `"anthropic"` and leaving the **existing AI-SDK code
path unchanged for `"deepseek"`** (Task 2 replaces that branch):

```ts
async function executeDefaultProvider(
  input: CallModelInput,
  providerName: string,
  model: string,
): Promise<ModelGatewayProviderResult> {
  switch (input.kind) {
    case "generate_text": {
      assertSupportedGenerationProvider(providerName);
      if (providerName === "anthropic") return generateTextAnthropic(input, model);
      // providerName === "deepseek" — still AI SDK, migrated in Task 2
      const result = await generateText({
        model: generationModel(providerName, model),
        prompt: input.prompt,
        system: input.system,
      });
      return {
        text: result.text,
        usage: result.totalUsage ?? result.usage,
        rawResponse: result.response.body ?? result.response,
      };
    }
    case "generate_object": {
      assertSupportedGenerationProvider(providerName);
      if (providerName === "anthropic") return generateObjectAnthropic(input, model);
      // providerName === "deepseek" — still AI SDK, migrated in Task 2
      const result = await generateObject({
        model: generationModel(providerName, model),
        prompt: input.prompt,
        system: input.system,
        schema: input.schema,
        schemaName: input.schemaName,
      });
      return {
        object: result.object,
        usage: result.usage,
        rawResponse: result.response.body ?? result.response,
      };
    }
    case "embed": {
      requireEnv("OPENAI_API_KEY");
      const result = await embed({ model: openai.embedding(model), value: input.value });
      return { embedding: result.embedding, usage: result.usage, rawResponse: result.response?.body ?? result.response };
    }
    case "embed_many": {
      requireEnv("OPENAI_API_KEY");
      const result = await embedMany({ model: openai.embedding(model), values: input.values });
      return { embeddings: result.embeddings, usage: result.usage, rawResponse: result.responses };
    }
  }
}
```

(The `embed`/`embed_many` cases and `generationModel()` are untouched in this
task — leave them exactly as they are today; Tasks 2 and 3 replace them.)

**Step 5: Create the mocked-Anthropic-client test** —
`tests/model-gateway-anthropic-provider.test.ts`:

```ts
import { z } from "zod";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

const { messagesCreateMock, anthropicCtorMock } = vi.hoisted(() => ({
  messagesCreateMock: vi.fn(),
  anthropicCtorMock: vi.fn(),
}));
vi.mock("@anthropic-ai/sdk", () => ({
  default: class {
    messages = { create: messagesCreateMock };
    constructor(opts: unknown) {
      anthropicCtorMock(opts);
    }
  },
}));

// Import after the mock so callModel picks up the mocked constructor.
const { callModel } = await import("@/lib/model-gateway");

async function resetLedgerTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

describe("callModel() — Anthropic raw SDK path", () => {
  const savedKey = process.env.ANTHROPIC_API_KEY;

  beforeEach(async () => {
    await resetLedgerTables();
    process.env.ANTHROPIC_API_KEY = "sk-ant-test-key";
    messagesCreateMock.mockReset();
    anthropicCtorMock.mockReset();
  });

  afterEach(async () => {
    if (savedKey === undefined) delete process.env.ANTHROPIC_API_KEY;
    else process.env.ANTHROPIC_API_KEY = savedKey;
    await closeDb();
  });

  it("constructs the client with the bare host (no /v1 suffix)", async () => {
    messagesCreateMock.mockResolvedValue({
      content: [{ type: "text", text: "hi" }],
      usage: { input_tokens: 1, output_tokens: 1 },
    });
    await callModel(getDb(), {
      kind: "generate_text",
      taskName: "t",
      promptVersion: "1",
      prompt: "hi",
      providerName: "anthropic",
      model: "claude-sonnet-4-6",
    });
    expect(anthropicCtorMock).toHaveBeenCalledWith(
      expect.objectContaining({ baseURL: "https://api.anthropic.com" }),
    );
  });

  it("generate_text concatenates text blocks and maps usage", async () => {
    messagesCreateMock.mockResolvedValue({
      content: [{ type: "text", text: "Hello " }, { type: "text", text: "world" }],
      usage: { input_tokens: 10, output_tokens: 5 },
    });
    const result = await callModel(getDb(), {
      kind: "generate_text",
      taskName: "t",
      promptVersion: "1",
      prompt: "hi",
      providerName: "anthropic",
      model: "claude-sonnet-4-6",
    });
    expect(result.text).toBe("Hello world");
    expect(result.usage).toMatchObject({ inputTokens: 10, outputTokens: 5, totalTokens: 15 });
    expect(messagesCreateMock).toHaveBeenCalledWith(
      expect.objectContaining({ max_tokens: 64000, model: "claude-sonnet-4-6" }),
    );
  });

  it("falls back to 4096 max_tokens for a non-sonnet-4 model family", async () => {
    messagesCreateMock.mockResolvedValue({
      content: [{ type: "text", text: "hi" }],
      usage: { input_tokens: 1, output_tokens: 1 },
    });
    await callModel(getDb(), {
      kind: "generate_text",
      taskName: "t",
      promptVersion: "1",
      prompt: "hi",
      providerName: "anthropic",
      model: "claude-haiku-4-5",
    });
    expect(messagesCreateMock).toHaveBeenCalledWith(
      expect.objectContaining({ max_tokens: 4096 }),
    );
  });

  it("generate_object forces a single tool call and extracts its input", async () => {
    messagesCreateMock.mockResolvedValue({
      content: [{ type: "tool_use", id: "toolu_1", name: "emit_object", input: { ok: true } }],
      usage: { input_tokens: 4, output_tokens: 2 },
    });
    const result = await callModel(getDb(), {
      kind: "generate_object",
      taskName: "t",
      promptVersion: "1",
      prompt: "hi",
      schema: z.object({ ok: z.boolean() }),
      providerName: "anthropic",
      model: "claude-sonnet-4-6",
    });
    expect(result.object).toEqual({ ok: true });
    const call = messagesCreateMock.mock.calls[0][0];
    expect(call.tool_choice).toEqual({ type: "tool", name: "emit_object" });
    expect(call.tools).toHaveLength(1);
    expect(call.tools[0].name).toBe("emit_object");
  });

  it("raises schema_error when the tool call's input fails schema validation", async () => {
    messagesCreateMock.mockResolvedValue({
      content: [{ type: "tool_use", id: "toolu_1", name: "emit_object", input: { ok: "not-a-bool" } }],
      usage: { input_tokens: 4, output_tokens: 2 },
    });
    await expect(
      callModel(getDb(), {
        kind: "generate_object",
        taskName: "t",
        promptVersion: "1",
        prompt: "hi",
        schema: z.object({ ok: z.boolean() }),
        providerName: "anthropic",
        model: "claude-sonnet-4-6",
      }),
    ).rejects.toMatchObject({ code: "schema_error" });
  });
});
```

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/model-gateway-anthropic-provider.test.ts tests/anthropic-base-url.test.ts tests/model-gateway-anthropic.test.ts tests/model-gateway.test.ts`
Expected: PASS — new tests plus the two pre-existing Anthropic/gateway test
files, all green (the two pre-existing files verify `config_error`/`missing_config`
short-circuit before any client is constructed — unaffected by this task).

**Step 6: Confirm the two live production `generate_object` callers keep their
effective token ceiling.**

```bash
grep -n 'callModel(.*kind: *"generate_object"\|kind: *"generate_object"' src/lib/harness.ts src/extractors/llm.ts
```

Both `src/lib/harness.ts`'s `decide()` (task `agent_react_decide`) and
`src/extractors/llm.ts`'s candidate extraction (task
`extract_memory_candidates`) call `callModel` with `providerName: "anthropic"`
and the configured `claude-sonnet-4-6` model at runtime (via
`SOURCECADO_GENERATION_PROVIDER`/`.env.example`) — confirm neither passes its
own token limit (none exists on `CallModelInput`), so both now resolve
`anthropicMaxTokensForModel("claude-sonnet-4-6")` → `64000`, unchanged from
the AI SDK's implicit default and *not* the 4096 fallback. Record this
explicitly as verified; it is not a code change to either file.

**Acceptance criteria:**
- `assertSupportedGenerationProvider` + the two new Anthropic functions exist
  in `src/lib/model-gateway.ts`; the `generate_text`/`generate_object` cases
  in `executeDefaultProvider` branch to them for `providerName === "anthropic"`.
- `toBareAnthropicHost` is exported and tested; `resolveAnthropicBaseUrl`'s
  own tests are untouched and still pass.
- `anthropicMaxTokensForModel` exists and resolves `64000` for
  `claude-sonnet-4-*` models (fallback `4096` for any other family) — Step 6
  confirms both live `generate_object` callers (`harness.ts`'s `decide()`,
  `extractors/llm.ts`) keep their current 64000 effective ceiling for the
  configured model, not the 4096 fallback.
- All listed test files pass; `npx tsc --noEmit` (or `npm run build`) has no
  new type errors attributable to this task (fix the `input_schema` cast if
  the installed SDK's exact type path differs — runtime behavior stays as
  written).

---

### Task 2: DeepSeek (OpenAI-compat) raw-SDK migration — `generate_text` + `generate_object`

**Build:** Install `openai`; replace the DeepSeek branch of
`executeDefaultProvider()`'s `generate_text`/`generate_object` cases with a
raw `openai` client pointed at DeepSeek's base URL; delete `generationModel()`
and the now-unused `@ai-sdk/deepseek`/`generateText`/`generateObject` imports
(the AI SDK's `embed`/`embedMany`/`openai` embedding import and `LanguageModel`
type stay for now — Task 3 removes those).

**Files:**
- Modify: `src/lib/model-gateway.ts`
- Create: `tests/model-gateway-deepseek-provider.test.ts`
- Modify: `package.json`, `package-lock.json` (via `npm install`)

**Step 1: Install the raw OpenAI SDK.**

```bash
npm install openai
```

Verify: `grep '"openai"' package.json` shows a version under `dependencies`.

**Step 2: Add the DeepSeek client + generation functions** in
`src/lib/model-gateway.ts`, near `anthropicClient()`:

```ts
import OpenAIClient from "openai"; // add to the import block; keep the existing
                                    // `import { openai } from "@ai-sdk/openai"` for now — Task 3 removes it

function deepseekClient(): OpenAIClient {
  requireEnv("DEEPSEEK_API_KEY");
  return new OpenAIClient({
    apiKey: process.env.DEEPSEEK_API_KEY,
    baseURL: "https://api.deepseek.com",
  });
}

async function generateTextDeepseek(input: GenerateTextInput, model: string): Promise<ModelGatewayProviderResult> {
  const client = deepseekClient();
  const messages: OpenAIClient.Chat.ChatCompletionMessageParam[] = [];
  if (input.system) messages.push({ role: "system", content: input.system });
  messages.push({ role: "user", content: input.prompt });
  const response = await client.chat.completions.create({
    model,
    max_tokens: DEFAULT_MAX_TOKENS,
    messages,
  });
  return {
    text: response.choices[0]?.message.content ?? "",
    usage: {
      inputTokens: response.usage?.prompt_tokens ?? null,
      outputTokens: response.usage?.completion_tokens ?? null,
      totalTokens: response.usage?.total_tokens ?? null,
    },
    rawResponse: response,
  };
}

async function generateObjectDeepseek(
  input: GenerateObjectInput,
  model: string,
): Promise<ModelGatewayProviderResult> {
  const client = deepseekClient();
  const jsonSchema = toGenerationJsonSchema(input.schema);
  const promptWithSchema = [
    input.prompt,
    "",
    "Respond with a single strict JSON object matching this schema and no other text:",
    JSON.stringify(jsonSchema),
  ].join("\n");
  const messages: OpenAIClient.Chat.ChatCompletionMessageParam[] = [];
  if (input.system) messages.push({ role: "system", content: input.system });
  messages.push({ role: "user", content: promptWithSchema });
  const response = await client.chat.completions.create({
    model,
    max_tokens: DEFAULT_MAX_TOKENS,
    response_format: { type: "json_object" },
    messages,
  });
  const raw = response.choices[0]?.message.content ?? "{}";
  let object: unknown;
  try {
    object = JSON.parse(raw);
  } catch (error) {
    throw new ModelGatewayError(
      "invalid_output",
      "Model provider returned malformed JSON for generate_object.",
      { cause: error },
    );
  }
  return {
    object,
    usage: {
      inputTokens: response.usage?.prompt_tokens ?? null,
      outputTokens: response.usage?.completion_tokens ?? null,
      totalTokens: response.usage?.total_tokens ?? null,
    },
    rawResponse: response,
  };
}
```

**Step 3: Update `executeDefaultProvider`'s `generate_text`/`generate_object`
cases** to call the new DeepSeek functions instead of the AI-SDK fallback path,
and **delete `generationModel()`** (no longer called by anything):

```ts
case "generate_text": {
  assertSupportedGenerationProvider(providerName);
  return providerName === "anthropic"
    ? generateTextAnthropic(input, model)
    : generateTextDeepseek(input, model);
}
case "generate_object": {
  assertSupportedGenerationProvider(providerName);
  return providerName === "anthropic"
    ? generateObjectAnthropic(input, model)
    : generateObjectDeepseek(input, model);
}
```

Delete the `generationModel()` function entirely, and remove its now-dead
imports: `import { deepseek } from "@ai-sdk/deepseek";` and `import {
generateObject, generateText } from "ai";` (keep `import { embed, embedMany }
from "ai"`, `import { openai } from "@ai-sdk/openai";`, and `import type {
LanguageModel } from "ai";` for now if `LanguageModel` is still referenced
anywhere — grep first; if `generationModel`'s deletion removes the only
usage, delete that import too).

**Step 4: Create the mocked-DeepSeek-client test** —
`tests/model-gateway-deepseek-provider.test.ts`:

```ts
import { z } from "zod";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

const { chatCreateMock, openaiCtorMock } = vi.hoisted(() => ({
  chatCreateMock: vi.fn(),
  openaiCtorMock: vi.fn(),
}));
vi.mock("openai", () => ({
  default: class {
    chat = { completions: { create: chatCreateMock } };
    embeddings = { create: vi.fn() };
    constructor(opts: unknown) {
      openaiCtorMock(opts);
    }
  },
}));

const { callModel } = await import("@/lib/model-gateway");

async function resetLedgerTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

describe("callModel() — DeepSeek raw SDK path", () => {
  const savedKey = process.env.DEEPSEEK_API_KEY;

  beforeEach(async () => {
    await resetLedgerTables();
    process.env.DEEPSEEK_API_KEY = "sk-deepseek-test-key";
    chatCreateMock.mockReset();
    openaiCtorMock.mockReset();
  });

  afterEach(async () => {
    if (savedKey === undefined) delete process.env.DEEPSEEK_API_KEY;
    else process.env.DEEPSEEK_API_KEY = savedKey;
    await closeDb();
  });

  it("constructs the client pointed at DeepSeek's base URL", async () => {
    chatCreateMock.mockResolvedValue({
      choices: [{ message: { content: "hi" } }],
      usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
    });
    await callModel(getDb(), {
      kind: "generate_text",
      taskName: "t",
      promptVersion: "1",
      prompt: "hi",
      providerName: "deepseek",
      model: "deepseek-chat",
    });
    expect(openaiCtorMock).toHaveBeenCalledWith(
      expect.objectContaining({ baseURL: "https://api.deepseek.com" }),
    );
  });

  it("generate_text maps choices[0].message.content and usage", async () => {
    chatCreateMock.mockResolvedValue({
      choices: [{ message: { content: "Hello there" } }],
      usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
    });
    const result = await callModel(getDb(), {
      kind: "generate_text",
      taskName: "t",
      promptVersion: "1",
      prompt: "hi",
      providerName: "deepseek",
      model: "deepseek-chat",
    });
    expect(result.text).toBe("Hello there");
    expect(result.usage).toMatchObject({ inputTokens: 10, outputTokens: 5, totalTokens: 15 });
  });

  it("generate_object sends response_format json_object and parses the result", async () => {
    chatCreateMock.mockResolvedValue({
      choices: [{ message: { content: JSON.stringify({ candidates: ["Ada"] }) } }],
      usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 },
    });
    const result = await callModel(getDb(), {
      kind: "generate_object",
      taskName: "t",
      promptVersion: "1",
      prompt: "extract",
      schema: z.object({ candidates: z.array(z.string()) }),
      providerName: "deepseek",
      model: "deepseek-chat",
    });
    expect(result.object).toEqual({ candidates: ["Ada"] });
    const call = chatCreateMock.mock.calls[0][0];
    expect(call.response_format).toEqual({ type: "json_object" });
  });

  it("raises invalid_output when the model returns malformed JSON", async () => {
    chatCreateMock.mockResolvedValue({
      choices: [{ message: { content: "not json" } }],
      usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
    });
    await expect(
      callModel(getDb(), {
        kind: "generate_object",
        taskName: "t",
        promptVersion: "1",
        prompt: "extract",
        schema: z.object({ candidates: z.array(z.string()) }),
        providerName: "deepseek",
        model: "deepseek-chat",
      }),
    ).rejects.toMatchObject({ code: "invalid_output" });
  });
});
```

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/model-gateway-deepseek-provider.test.ts tests/model-gateway-anthropic.test.ts tests/model-gateway.test.ts`
Expected: PASS — new tests plus both pre-existing gateway test files (the
provider-injected fake-provider tests in `model-gateway.test.ts` are
unaffected since they bypass `executeDefaultProvider` via the `provider` seam).

**Acceptance criteria:**
- `deepseekClient`, `generateTextDeepseek`, `generateObjectDeepseek` exist;
  `executeDefaultProvider`'s two generation cases call the Anthropic or
  DeepSeek function based on `providerName`, with no remaining call to
  `generationModel` (function deleted) or to AI SDK's `generateText`/`generateObject`.
- `grep -n "generateText\|generateObject" src/lib/model-gateway.ts` shows no
  matches (the AI SDK functions are gone; the new function *names*
  `generateTextAnthropic`/`generateTextDeepseek`/etc. don't collide with this
  grep pattern only if you also check imports — confirm via
  `grep -n 'from "ai"' src/lib/model-gateway.ts` that `generateText`/`generateObject`
  are no longer imported).
- All listed test files pass; `npm run build` / `tsc --noEmit` clean.

---

### Task 3: Embedding raw-SDK migration — `embed` + `embed_many`

**Build:** Replace the `embed`/`embed_many` cases of `executeDefaultProvider()`
with raw `openai` embeddings calls; remove the now-fully-unused
`@ai-sdk/openai`, `@ai-sdk/anthropic`, and `ai` imports from
`model-gateway.ts` (this is the last of the four kinds to migrate — after
this task nothing in the file imports the AI SDK).

**Files:**
- Modify: `src/lib/model-gateway.ts`
- Create: `tests/model-gateway-embed-provider.test.ts`

**Step 1: Add the OpenAI embedding client + functions** in
`src/lib/model-gateway.ts`:

```ts
function openaiEmbeddingClient(): OpenAIClient {
  requireEnv("OPENAI_API_KEY");
  return new OpenAIClient({ apiKey: process.env.OPENAI_API_KEY });
}

async function embedOpenai(input: EmbedInput, model: string): Promise<ModelGatewayProviderResult> {
  const client = openaiEmbeddingClient();
  const response = await client.embeddings.create({ model, input: input.value });
  return {
    embedding: response.data[0]?.embedding,
    usage: { tokens: response.usage?.total_tokens ?? null },
    rawResponse: response,
  };
}

async function embedManyOpenai(input: EmbedManyInput, model: string): Promise<ModelGatewayProviderResult> {
  const client = openaiEmbeddingClient();
  const response = await client.embeddings.create({ model, input: input.values });
  const embeddings = [...response.data]
    .sort((a, b) => a.index - b.index)
    .map((entry) => entry.embedding);
  return {
    embeddings,
    usage: { tokens: response.usage?.total_tokens ?? null },
    rawResponse: response,
  };
}
```

**Step 2: Update `executeDefaultProvider`'s `embed`/`embed_many` cases:**

```ts
case "embed":
  return embedOpenai(input, model);
case "embed_many":
  return embedManyOpenai(input, model);
```

**Step 3: Remove the now-fully-unused imports** at the top of
`src/lib/model-gateway.ts`:

```ts
// Delete these three lines entirely:
// import { createAnthropic } from "@ai-sdk/anthropic";
// import { openai } from "@ai-sdk/openai";
// import { embed, embedMany } from "ai";
// import type { LanguageModel } from "ai";   (if still present — generationModel, its only user, was deleted in Task 2)
```

Also remove the now-dead `requireEnv("OPENAI_API_KEY")` calls that used to
live inline in the old `embed`/`embed_many` cases — they're folded into
`openaiEmbeddingClient()` now. Run
`grep -n '"ai"\|@ai-sdk' src/lib/model-gateway.ts` and confirm **zero
matches** — this file no longer references the AI SDK at all.

**Step 4: Create the mocked-embeddings test** —
`tests/model-gateway-embed-provider.test.ts`:

```ts
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

const { embeddingsCreateMock, openaiCtorMock } = vi.hoisted(() => ({
  embeddingsCreateMock: vi.fn(),
  openaiCtorMock: vi.fn(),
}));
vi.mock("openai", () => ({
  default: class {
    chat = { completions: { create: vi.fn() } };
    embeddings = { create: embeddingsCreateMock };
    constructor(opts: unknown) {
      openaiCtorMock(opts);
    }
  },
}));

const { callModel } = await import("@/lib/model-gateway");

async function resetLedgerTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

describe("callModel() — OpenAI embeddings raw SDK path", () => {
  const savedKey = process.env.OPENAI_API_KEY;

  beforeEach(async () => {
    await resetLedgerTables();
    process.env.OPENAI_API_KEY = "sk-openai-test-key";
    embeddingsCreateMock.mockReset();
    openaiCtorMock.mockReset();
  });

  afterEach(async () => {
    if (savedKey === undefined) delete process.env.OPENAI_API_KEY;
    else process.env.OPENAI_API_KEY = savedKey;
    await closeDb();
  });

  it("embed maps data[0].embedding and usage.total_tokens", async () => {
    embeddingsCreateMock.mockResolvedValue({
      data: [{ index: 0, embedding: [0.1, 0.2, 0.3] }],
      usage: { prompt_tokens: 3, total_tokens: 3 },
    });
    const result = await callModel(getDb(), {
      kind: "embed",
      taskName: "t",
      promptVersion: "1",
      value: "hello",
      providerName: "openai",
      model: "text-embedding-3-small",
    });
    expect(result.embedding).toEqual([0.1, 0.2, 0.3]);
    expect(result.usage).toMatchObject({ tokens: 3 });
  });

  it("embed_many re-sorts by index before mapping embeddings", async () => {
    embeddingsCreateMock.mockResolvedValue({
      data: [
        { index: 1, embedding: [0.2] },
        { index: 0, embedding: [0.1] },
      ],
      usage: { prompt_tokens: 2, total_tokens: 2 },
    });
    const result = await callModel(getDb(), {
      kind: "embed_many",
      taskName: "t",
      promptVersion: "1",
      values: ["a", "b"],
      providerName: "openai",
      model: "text-embedding-3-small",
    });
    expect(result.embeddings).toEqual([[0.1], [0.2]]);
  });

  it("requires OPENAI_API_KEY", async () => {
    delete process.env.OPENAI_API_KEY;
    await expect(
      callModel(getDb(), {
        kind: "embed",
        taskName: "t",
        promptVersion: "1",
        value: "hello",
        providerName: "openai",
        model: "text-embedding-3-small",
      }),
    ).rejects.toMatchObject({ code: "missing_config" });
  });
});
```

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/model-gateway-embed-provider.test.ts tests/model-gateway.test.ts`
Expected: PASS (the embed-dimension test in `model-gateway.test.ts` uses the
fake-provider seam, unaffected).

**Acceptance criteria:**
- `embedOpenai`/`embedManyOpenai`/`openaiEmbeddingClient` exist;
  `executeDefaultProvider`'s embed cases call them.
- `grep -n '"ai"\|@ai-sdk' src/lib/model-gateway.ts` returns **zero matches**.
- All listed test files pass; `npm run build` clean.

---

### Task 4: Regression verification — `extractors/llm.ts` + `memory/embed.ts`

**Build:** Nothing — this task is verification-only. Both files call
`callModel(...)` and never import `ai`/`@ai-sdk/*` directly (confirmed in
Context above), so Tasks 1–3 change their runtime behavior only through
`callModel`'s internals, which their existing tests don't exercise (they
inject a `provider`/`config.provider` seam). If any of the commands below
fail, that is a real regression from Tasks 1–3 — fix it in
`model-gateway.ts`, not by editing `extractors/llm.ts` or `memory/embed.ts`
(their file ownership is unchanged; a fix belongs in the gateway).

**Files:**
- No modifications expected. If a regression forces a change, it must be to
  `src/lib/model-gateway.ts` (Tasks 1–3's code), not these two files.

**Step 1: Run every test file that touches either module.**

```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run tests/extractors.test.ts tests/memory-extract.test.ts tests/memory-embed.test.ts
```

Expected: PASS — same pass count as before Task 1 (these suites don't gain or
lose tests in this slice).

**Step 2: Confirm neither file references the AI SDK** (a documentation
check, not expected to change anything):

```bash
grep -n '"ai"\|@ai-sdk' src/extractors/llm.ts src/lib/memory/embed.ts
```

Expected: no output (zero matches) — confirms both files were already
gateway-only consumers before this slice, and stay that way.

**Acceptance criteria:**
- `tests/extractors.test.ts`, `tests/memory-extract.test.ts`,
  `tests/memory-embed.test.ts` all pass, unchanged pass/fail counts from
  before this slice.
- `src/extractors/llm.ts` and `src/lib/memory/embed.ts` have zero diff
  against their pre-R8 state (verify with `git diff --stat src/extractors/llm.ts src/lib/memory/embed.ts`
  showing no output) unless a genuine regression required a fix, in which
  case the fix must be documented in the commit message and still land in
  `model-gateway.ts`.

---

### Task 5: Remove `ai` + `@ai-sdk/*` from `package.json`

**Build:** A repo-wide guard check, then (only if the guard passes)
`npm uninstall` of the four AI SDK packages plus a regression test asserting
they're gone from `package.json`.

**Files:**
- Modify: `package.json`, `package-lock.json` (only if the guard in Step 1 passes)
- Create: `tests/package-deps.test.ts`

**Step 1: Run the repo-wide guard** (matches real import statements, not
comment text — see Reconciliation note):

```bash
# @ai-sdk/* importers (the packages R8 removes):
grep -rlE 'from "@ai-sdk/' src
# `ai` importers (must be ONLY ui-message-stream.ts — R5's SSE transport, kept):
grep -rlE 'from "ai"' src
```

- **`@ai-sdk/*` importers must be zero** after Tasks 1–3 (only
  `model-gateway.ts` imported them). If zero → proceed to Step 2.
- **`from "ai"` importers must be exactly `src/lib/ui-message-stream.ts`.**
  That is the expected, allowed steady state (R5's SSE transport;
  `tests/model-boundary.test.ts` allows it). `ai` is NOT removed this slice.
- **Any other file in either list:** stop and investigate — an unexpected
  importer exists that Tasks 1–3 didn't account for.

**Step 2 (guard passed): remove only the `@ai-sdk/*` packages.**

```bash
npm uninstall @ai-sdk/anthropic @ai-sdk/deepseek @ai-sdk/openai
```

Verify: `grep -E '@ai-sdk/' package.json` returns no output; `grep '"ai"'
package.json` still shows `ai` (kept for the SSE transport).

**Step 3: Add the regression test** — `tests/package-deps.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { join } from "node:path";

describe("package.json dependencies", () => {
  const pkg = JSON.parse(readFileSync(join(process.cwd(), "package.json"), "utf8"));
  const depNames = [
    ...Object.keys(pkg.dependencies ?? {}),
    ...Object.keys(pkg.devDependencies ?? {}),
  ];

  it("does not depend on the @ai-sdk/* provider packages", () => {
    const aiSdkDeps = depNames.filter((name) => name.startsWith("@ai-sdk/"));
    expect(aiSdkDeps).toEqual([]);
  });

  it("still depends on `ai` (R5's ui-message-stream SSE transport)", () => {
    // Full removal of `ai` is ticketed separately (rewrite the SSE transport
    // off createUIMessageStream). Until then `ai` is a deliberate dependency.
    expect(pkg.dependencies).toHaveProperty("ai");
  });

  it("depends on the raw Anthropic and OpenAI SDKs", () => {
    expect(pkg.dependencies).toHaveProperty("@anthropic-ai/sdk");
    expect(pkg.dependencies).toHaveProperty("openai");
  });
});
```

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/package-deps.test.ts`
Expected: PASS — `@ai-sdk/*` gone, `ai` + the two raw SDKs present.

**Acceptance criteria:**
- `package.json` has no `@ai-sdk/*` entries; `ai`, `@anthropic-ai/sdk`, and
  `openai` are all present; `tests/package-deps.test.ts` exists and passes.
- `npm run build` and the full test suite (Task 6) still pass after the
  uninstall.
- The follow-up ticket to remove `ai` (migrate `ui-message-stream.ts` off the
  SSE-transport helpers) exists at
  `docs/superpowers/plans/2026-07-21-ticket-remove-ai-sdk-ui-stream.md`.

---

### Task 6: Full verification pass

**Files:** None (verification only).

**Step 1: Full test suite.**

```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run
```

Expected: PASS — all pre-existing suites plus the 5 new/modified R8 test
files (`anthropic-base-url.test.ts` additions, `model-gateway-anthropic-provider.test.ts`,
`model-gateway-deepseek-provider.test.ts`, `model-gateway-embed-provider.test.ts`,
and `package-deps.test.ts` if Task 5's guard passed) all green.

**Step 2: Lint.**

```bash
npm run lint
```

Expected: `✔ No ESLint warnings or errors`.

**Step 3: Build.**

```bash
npm run build
```

Expected: build succeeds with no type errors.

**Step 4: Cleanup pass over this slice's own additions.**

Re-read the diff for `src/lib/model-gateway.ts` end to end. Confirm:
- No leftover AI-SDK imports, no dead `generationModel`/AI-SDK helper code.
- `DEFAULT_MAX_TOKENS`, `anthropicMaxTokensForModel`, `anthropicClient`,
  `deepseekClient`, `openaiEmbeddingClient`, `toBareAnthropicHost`,
  `assertSupportedGenerationProvider`, `toGenerationJsonSchema`, and the six
  `generate*`/`embed*` provider functions are each used exactly once from
  `executeDefaultProvider` (directly or via the `generate*Anthropic`
  functions, for `anthropicMaxTokensForModel`/`DEFAULT_MAX_TOKENS`) — no
  orphaned helpers.
- `CallModelInput`/`CallModelResult`/`ModelGatewayProvider`/`NormalizedUsage`
  are byte-for-byte unchanged from before this slice (external contract
  preserved) — verify with `git diff` showing no changes to those
  type/interface declarations.

**Step 5: Manual smoke (optional, requires real API keys).**

```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
export ANTHROPIC_API_KEY=<real key>
export SOURCECADO_GENERATION_PROVIDER=anthropic
npm run ingest -- --help  # or any script exercising extractLlmCandidates end-to-end
```

Expected: a real call succeeds against the live Anthropic API through the new
raw-SDK path (confirms the tool-forcing `generate_object` path actually works
against the real API, not just mocks). Skip if no key is available — the
mocked tests in Tasks 1–3 are the source of truth for merge-readiness.

**Acceptance criteria:**
- Full suite green, lint clean, build clean.
- No orphaned code from this slice's own additions.
- Spec Acceptance Criterion #9 satisfied (`ai`/`@ai-sdk/*` absent from
  `package.json`) **or** explicitly deferred per Task 5's guard, matching the
  spec's own allowance ("or explicitly ticketed if cut").

---

## Tests

| File | New/Modified | Covers |
|---|---|---|
| `tests/anthropic-base-url.test.ts` | Modified (append) | `toBareAnthropicHost` — strips `/v1`/`/v2`, no-op without a version segment, composes with `resolveAnthropicBaseUrl` |
| `tests/model-gateway-anthropic-provider.test.ts` | New | Anthropic raw-SDK path: bare-host client construction, `generate_text` text-block concatenation + usage mapping, `anthropicMaxTokensForModel` ceiling (64000 for `claude-sonnet-4-6`, 4096 fallback for other families), `generate_object` tool-forcing (schema sent, `tool_choice` forced, `tool_use.input` extracted), schema-validation failure → `schema_error` |
| `tests/model-gateway-deepseek-provider.test.ts` | New | DeepSeek raw-SDK path: base URL, `generate_text` mapping, `generate_object` `response_format: json_object` + JSON parse, malformed JSON → `invalid_output` |
| `tests/model-gateway-embed-provider.test.ts` | New | OpenAI embeddings raw-SDK path: `embed` mapping, `embed_many` index-sorted mapping, missing `OPENAI_API_KEY` → `missing_config` |
| `tests/package-deps.test.ts` | New (only if Task 5's guard passes) | `package.json` has no `ai`/`@ai-sdk/*` deps; has `@anthropic-ai/sdk` + `openai` |
| `tests/model-gateway-anthropic.test.ts`, `tests/model-gateway.test.ts`, `tests/extractors.test.ts`, `tests/memory-extract.test.ts`, `tests/memory-embed.test.ts` | Unmodified — regression only | Pre-existing config-resolution and fake-provider-seam behavior stays green throughout |

No new test infrastructure is needed beyond `vi.mock`/`vi.hoisted` on the two
new SDK packages — same pattern already used for provider injection
elsewhere in the test suite (`vi.fn<ModelGatewayProvider>()`).

## Self-Review

**Spec coverage:**
- "Migrate `embed`/`embed_many` to the raw `openai` client" → Task 3.
- "remaining `generate_text`/`generate_object` callers (extractors, memory
  answer path)" → resolved as: the only remaining `generate_object` caller by
  R8's execution time is `src/extractors/llm.ts`, and it calls through
  `callModel` with no direct AI-SDK dependency, so migrating `callModel`'s
  internals (Tasks 1–2) is the actual migration; Task 4 verifies no residual
  breakage. The "memory answer path" runs through `harness.ts`/`agent-loop.ts`
  (R2/R4-owned, already on the new `streamAgentTurn` contract by the time R8
  runs) and is out of this slice's file ownership.
- "structured output via tool-forcing on Anthropic, `response_format` on
  OpenAI-compat" → Task 1 (tool-forcing) and Task 2 (`response_format:
  json_object`), verified against the configured model (`claude-sonnet-4-6`)
  not being on Anthropic's native-structured-output allowlist.
- "remove `ai` + `@ai-sdk/*` from package.json" → Task 5, with an explicit
  guard against the one known cross-slice blocker (`ui-message-stream.ts`,
  R5-owned) and the spec's own "or explicitly ticketed if cut" escape hatch
  exercised if that guard doesn't pass.
- Acceptance Criterion #9 → Task 5 + Task 6.
- Acceptance Criterion #10 (existing tests stay green after every slice) →
  Task 4 (regression-only verification) + Task 6 (full suite).

**Placeholder scan:** No TBD/TODO left in any task; the one explicitly
conditional step (Task 5) spells out both branches concretely rather than
leaving "handle appropriately."

**Type consistency:** `CallModelInput`/`GenerateTextInput`/`GenerateObjectInput`/
`EmbedInput`/`EmbedManyInput`/`CallModelResult`/`ModelGatewayProviderResult`/
`NormalizedUsage`/`ModelGatewayError` are all pre-existing types from
`src/lib/model-gateway.ts`, used identically across Tasks 1–3 — none are
redefined or changed shape. The new internal-only symbols
(`anthropicClient`, `deepseekClient`, `openaiEmbeddingClient`,
`toBareAnthropicHost`, `assertSupportedGenerationProvider`,
`toGenerationJsonSchema`, `DEFAULT_MAX_TOKENS`, `anthropicMaxTokensForModel`,
and the six `generate*Anthropic`/`generate*Deepseek`/`embed*Openai` functions) are used
consistently by name across all three provider-migration tasks and the final
cleanup pass (Task 6, Step 4).

---

## Eng Review (2026-07-14)

**Verdict: approve (revised).** The plan's file-level claims about current repo state
were verified byte-for-byte against `src/lib/model-gateway.ts`,
`src/extractors/llm.ts`, the four affected test files, `tests/model-boundary.test.ts`,
and `node_modules/@ai-sdk/anthropic` / `@ai-sdk/deepseek` — every factual
claim checked out except one, but that one is a real production regression
risk, not a nitpick. Baseline test run confirms the plan's starting point:

```
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run tests/model-gateway-anthropic.test.ts tests/model-gateway.test.ts \
  tests/anthropic-base-url.test.ts tests/model-boundary.test.ts tests/extractors.test.ts \
  tests/memory-extract.test.ts tests/memory-embed.test.ts
# 7 files, 51 tests, all green
```

Also confirmed live: neither R1 (`src/lib/llm/`) nor `@anthropic-ai/sdk`/`openai`
packages exist in this repo yet, and `src/lib/ui-message-stream.ts` still
imports from `"ai"` — so the plan's self-contained Task 1/2 package installs
and Task 5's "guard doesn't pass, stop short" branch are the actual paths this
plan will take if executed now, not the optimistic branch. The plan's
diagrams of both outcomes are correct.

### Must fix

1. **`DEFAULT_MAX_TOKENS = 4096` silently cuts the live Anthropic generation
   ceiling by ~16x for the exact configured model, and both current
   production callers of `generate_object` are affected.**
   `node_modules/@ai-sdk/anthropic/dist/index.js` (the code being replaced)
   resolves an *implicit per-model* `max_tokens` when no caller passes one:
   for any `claude-sonnet-4-*` model — including `claude-sonnet-4-6`, the
   model in `.env.example` — that implicit default is **64000**, not 4096
   (`maxOutputTokensForModel` table, `dist/index.js:5150`). The plan's
   Judgment-calls section reasons "no caller passes a token limit today, so
   this plan adds one fixed internal constant" — true that no caller passes
   one explicitly, but false that there was no effective limit: the AI SDK
   was already supplying a much larger one per-model. Two live, currently
   shipped call sites go through this exact path today:
   - `src/lib/harness.ts:184` (`decide()`, task `agent_react_decide`) — this
     is the ReAct agent-loop decision call behind the already-shipped
     Research Chat (git log: `74357d5 feat(A6.3): multi-turn conversation
     history in the agent loop`, `d7ea580 feat(A6.3): streaming multi-turn
     Research Chat with live reasoning trace`). Its schema includes a
     free-text final answer; a cited, multi-paragraph research answer plus
     tool-call JSON scaffolding can plausibly exceed 4096 tokens where it
     never would have hit 64000.
   - `src/extractors/llm.ts:197` (`extract_memory_candidates`) — extracts a
     `candidates` array (each with a verbatim `evidenceText` quote) from an
     entire ingested document. Any moderately long source (an email thread,
     a Drive doc) can produce an output well past 4096 tokens.
   R8 is explicitly "last, independent" and can execute before R2 replaces
   `harness.ts`'s decision call — so this is not a hypothetical future
   regression, it is a regression against the *currently deployed* behavior
   the moment this plan's Task 1 merges. The plan's Self-Review section
   assumes "the memory answer path... is out of this slice" because R2 will
   have superseded `harness.ts` by the time R8 runs — that assumption isn't
   backed by a dependency edge in the contracts brief (R8 is marked
   independent precisely so it *doesn't* need to wait), and isn't true of the
   repo as it stands today.
   **Fix:** don't hardcode one constant across both providers/kinds. Either
   (a) port a small per-model-family ceiling table for Anthropic (mirroring
   what `@ai-sdk/anthropic` already does — `claude-sonnet-4-*``→ 64000,
   fallback 4096), or (b) pick one high constant for Anthropic close to
   today's effective ceiling (e.g. 8192–64000, not 4096) while leaving
   DeepSeek's 4096 as-is (that half of the constant is well-justified — see
   Notes). Whichever direction, add a task step that greps
   `src/lib/harness.ts` and `src/extractors/llm.ts` for `callModel(...
   kind: "generate_object")` call sites and states explicitly that their
   effective token ceiling is unchanged or intentionally changed, with a
   reason.

   **Resolved:** the plan now uses option (a) — a per-model-family ceiling
   function, `anthropicMaxTokensForModel()` (`claude-sonnet-4-*` → 64000,
   fallback 4096), used by both `generateTextAnthropic` and
   `generateObjectAnthropic` in place of the flat constant. DeepSeek keeps
   `DEFAULT_MAX_TOKENS = 4096` unchanged (already reproduces
   `@ai-sdk/deepseek`'s existing no-explicit-limit behavior). Task 1 gained a
   Step 6 that greps `src/lib/harness.ts` and `src/extractors/llm.ts` for
   their `generate_object` call sites and confirms both resolve to 64000, not
   4096. The Judgment-calls section, Task 1's code/tests/acceptance criteria,
   and Task 6's cleanup-pass/Self-Review symbol lists are all updated to
   match.

2. **Dangling `openQuestions` cross-references — the section doesn't exist.**
   Lines 119, 1054, and 1107 all point to `openQuestions` as the durable
   record of the "Task 5 guard doesn't pass, R5 hasn't landed" gap ("see
   `openQuestions`", "this plan's `openQuestions` already does so"). `grep -n
   "## " ` on this file shows no `## Open Questions` heading anywhere — the
   only place this gap is actually described is prose inside "Judgment
   calls." Since we've confirmed Task 5's guard **will** hit the stop-short
   branch if this plan runs today (`ui-message-stream.ts` still imports
   `"ai"`), this isn't a cosmetic gap — it's the one place a future
   read-the-plan-cold engineer would look for "why didn't Task 5 finish" and
   find nothing. Fix: either add a real `## Open Questions` section (cheap,
   and matches this repo's convention of naming deferred/ticketed gaps
   explicitly elsewhere, e.g. the contracts brief's "Deferred to v2"
   section), or change all three references to point at "Judgment calls."

   **Resolved:** all three dangling references (the Judgment-calls bullet
   itself, Task 5's Step 1, and Task 5's "guard did not pass" acceptance
   criteria) now point at "Judgment calls" instead of a nonexistent
   `openQuestions` section — no new heading was added, since the gap was
   already fully described in prose there.

### Should fix

3. **`tests/model-boundary.test.ts` isn't mentioned anywhere in the plan**,
   despite being the one existing governance test whose entire purpose is
   auditing the exact boundary this plan dismantles (it allowlists AI-SDK
   imports to exactly `model-gateway.ts` and `ui-message-stream.ts`, skipping
   both from its scan). Confirmed it still passes mechanically after this
   migration regardless of what `model-gateway.ts` imports internally
   (allowlisted files are skipped, not required to still import `"ai"`), so
   this isn't a functional break — but a plan that's this thorough about
   citing every other affected test file should list this one in the `##
   Tests` table too, and Task 6 should say explicitly that the allowlist
   comment ("AI SDK surface is contained to two audited boundary modules")
   becomes half-stale the moment Task 3 lands (until Task 5 also lands,
   `model-gateway.ts` is allowlisted for a `"ai"` import it no longer has) —
   worth a one-line comment note in that test, not a functional fix.

4. **No test or code path distinguishes an Anthropic `max_tokens`-truncated
   response from a genuinely malformed one.** If a forced tool call gets cut
   off mid-argument by hitting `max_tokens` (more likely once Must-Fix #1 is
   addressed but budgets are still finite), `response.content` may lack a
   complete `tool_use` block, and today's code surfaces that as a generic
   `schema_error`/`toolUse?.input` being `undefined` with no signal that the
   root cause was a token ceiling, not a bad model response. Recommend
   surfacing `response.stop_reason` (Anthropic) / the DeepSeek equivalent in
   the thrown `ModelGatewayError`'s cause or in `model_calls.error_json`, so
   this failure mode is diagnosable from the ledger without re-deriving it
   from raw JSON blobs.

### Notes

- The DeepSeek half of `DEFAULT_MAX_TOKENS = 4096` is well-justified and
  doesn't need to change: `@ai-sdk/deepseek`'s current code
  (`dist/index.js:478`) passes `max_tokens: undefined` when no caller
  specifies one, meaning DeepSeek's API is already falling back to its own
  server-side default (documented as 4096 for `deepseek-chat`) today — so
  the raw-SDK migration reproduces existing behavior exactly for that
  provider. Must-fix #1 is specifically about the Anthropic constant.
- `resolveAnthropicBaseUrl`/`toBareAnthropicHost` composition, the DeepSeek
  base-URL claim (verified against the installed `@ai-sdk/deepseek` package:
  default is the bare host, no `/v1` injected), the `z.toJSONSchema`
  value-import change, the `normalizeUsage()` shape-reuse claims, and the
  `embeddings.create()` index-resort are all verified accurate against the
  current codebase — no changes needed there.
- Task ordering (per-branch replacement inside one `switch`, leaving
  untouched branches on the old AI SDK path until their own task) is a clean
  incremental/strangler-fig sequence — each task's intermediate state keeps
  the full suite green and is independently revertible. No sequencing change
  needed once Must-Fix #1 is resolved.
- Contracts-brief conformance: file ownership (§7), scope boundaries (no
  touches to `harness.ts`, `agent-loop.ts`, `tools/orchestrator.ts`,
  `context.ts`, chat session files), and the "R8 last, independent" ordering
  are all honored by what this plan edits. Must-fix #1 is a runtime-behavior
  gap, not an ownership violation — `callModel`'s type-level contract really
  is preserved byte-for-byte as claimed; its implicit token-budget behavior
  is not, and that distinction is what the plan's own "zero change to
  callModel's external contract" framing missed.
