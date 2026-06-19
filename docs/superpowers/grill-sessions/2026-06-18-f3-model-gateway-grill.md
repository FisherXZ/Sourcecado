# F3 Model Gateway Grill Session

Date: 2026-06-18

This note captures the current state of the F3 grill session so the thread can resume without re-litigating settled branches.

## Scope Being Grilled

F3 from `docs/superpowers/plans/2026-06-15-sourcecado-full-agent-stack-task-breakdown.md`:

- Build the single Model Gateway entry point from ADR-0004.
- Record named model task, prompt/version name, usage, status, and errors in `model_calls`.
- Return real provider responses.
- Provide structured-output parsing and error capture.

Relevant domain docs:

- `CONTEXT.md` already defines `Model Gateway` as the Sourcecado boundary for product model calls.
- `docs/adr/0004-single-model-gateway.md` says model calls should not be scattered across memory, chat, routines, tools, and validation.

## Settled Decisions

1. The gateway should be model-agnostic at the Sourcecado boundary.
2. F3 should use the Vercel AI SDK as the TypeScript-native transport layer.
3. F3 should support both generation and embeddings, not generation only.
4. Generation can prioritize cost flexibility with DeepSeek-compatible models.
5. Embeddings should use OpenAI `text-embedding-3-small`.
6. The embedding dimension is therefore 1536, which should inform later pgvector schema work.
7. The gateway may need two provider credentials: one for generation and one for embeddings.
8. Callers should pass `taskName` and `promptVersion`; the gateway should also store a hash of the actual prompt content.
9. F3 should not add a central prompt registry.
10. F3 and F4 should be implemented together as one vertical slice, so `model_calls` can link directly to Run Ledger records from the start.
11. The Run Ledger should follow a trace/span shape: `runs` are top-level executions, `run_steps` are hierarchical typed spans/observations, and `model_calls` / `tool_calls` are specialized detail rows linked to the relevant step.
12. The Run Ledger should copy Vercel AI SDK / LangSmith / OpenAI's simple default: capture model/tool inputs and outputs by default, with an explicit per-call option to suppress or redact sensitive payloads.
13. `run_steps.step_kind` should use a small tracing vocabulary, while sourcing-specific intent belongs in `run_steps.name`.
14. Use a simple Sourcecado lifecycle status for durable rows. `running`, `succeeded`, `failed`, and `cancelled` can apply to runs, steps, model calls, and tool calls; `skipped` should mainly apply to `run_steps` when a planned branch is intentionally not executed.

## Codebase Facts

- `src/extractors/llm.ts` currently calls OpenAI's Responses API directly.
- `src/extractors/llm.ts` already records a version-like value and a `promptHash` for the extraction prompt.
- `src/embeddings.ts` currently uses a fake 64-dimensional hash embedding.
- The F3 plan requires `model_calls`, but F4 owns the full Run Ledger tables.

## Current Unresolved Question

What raw model/tool input and output should the Run Ledger store?

Research-grounded recommendation:

- Capture raw model/tool inputs and outputs by default in F3+F4, because that is what Vercel AI SDK telemetry, LangSmith, and OpenAI Agents tracing all optimize for.
- Keep this simple in schema: `run_steps.input_json`, `run_steps.output_json`, `model_calls.request_json`, `model_calls.response_json`, `tool_calls.arguments_json`, and `tool_calls.result_json`.
- Add a small `capture_payloads` / `redaction_mode` option at the gateway/tool-call boundary so restricted cases can suppress or redact payloads.
- Also keep summaries if useful for the inspector, but do not make summary-only the default.

Reasoning:

- Raw payloads are what make traces useful for debugging agent behavior.
- The privacy concern is real for Sourcecado because the domain includes Restricted Material and Outreach History, but the industry pattern is not "summary only." It is "capture by default, with controls to hide/redact sensitive inputs and outputs."
- This keeps F3+F4 simple while preserving the option to avoid storing sensitive payloads when a specific source/tool/task requires it.

Research sources checked:

- Sourcecado ADR-0002: Run Ledger is the product-owned observability source of truth.
- Sourcecado ADR-0004: Model Gateway records named tasks, prompt/version names, usage, errors, structured parsing, and Run Ledger links.
- OpenAI Agents SDK tracing: trace = end-to-end workflow, spans = operations including generation and function tool calls.
- LangSmith observability concepts: trace = single operation, run = span for a unit of work such as LLM, prompt formatting, retrieval, or another discrete operation.
- Langfuse concepts: trace = request/operation, observations = individual steps such as generations, tool calls, and RAG retrieval, with nesting.
- OpenInference semantic conventions: span kinds include LLM, EMBEDDING, CHAIN, RETRIEVER, RERANKER, TOOL, AGENT, PROMPT, EVALUATOR.
- Vercel AI SDK telemetry: records spans for generateText, provider generate calls, tool calls, embed, and embedMany, with functionId/metadata and token usage.

## Not Captured In CONTEXT.md

These are implementation decisions, not glossary terms. `CONTEXT.md` should stay implementation-free unless the session resolves new domain language.
