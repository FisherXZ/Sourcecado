# F5 — Agent Harness ReAct Loop: Design

Date: 2026-06-23
Status: Approved for implementation
Slice: F5 (Foundation cluster) from
`docs/superpowers/plans/2026-06-15-sourcecado-full-agent-stack-task-breakdown.md`
Blocked by: F3 (Model Gateway), F4 (Run Ledger) — both merged to `main` (PR #6).

## Purpose

Build the ReAct-style tool-use loop that turns the F3 gateway and F4 ledger into a
runnable agent: a run executes a multi-step loop, calls the model through the
gateway, invokes registered tools through a permission-gated registry, and writes
the entire trace to the Run Ledger so it renders in the existing run inspector.

This is the spine. Nothing sourcing-specific yet — the only tool is `echo`.

## Decisions (locked during brainstorming)

1. **Tool selection = structured output.** Each iteration the model returns a typed
   decision via `callModel(kind: "generate_object")` against a discriminated union
   (`tool` or `final`). Reuses the existing gateway and Zod; no gateway changes;
   deterministic and testable with the mock provider. (Rejected: native tool-calling
   API — needs gateway extension; free-text ReAct parsing — brittle.)
2. **Permission model = allowed-set per run, allow/deny only.** A run is started with
   an explicit set of allowed permission classes. A tool whose class is not in the
   set is refused and logged. No ranked hierarchy (the six classes do not form a
   meaningful linear order). No interactive `ask` tier — F5 runs are headless
   (ADR-0003 manual-first; the human gate is at artifact review, not mid-loop).
3. **UI scope = thin trigger + existing inspector.** F5 ships a minimal API route
   (`POST` a question → run the loop → return a run id). The rich Research Chat UI is
   Feature A6. The existing `/runs/[id]` inspector renders the trace unchanged.
4. **Loop guardrails (Option 1, validated against Claude Code's loop):**
   - terminate on model `{action: "final"}` → run succeeds.
   - iteration cap (default 8) → run fails with `max_steps_exceeded`.
   - tool error / permission refusal / invalid args → recorded as a failed tool call
     AND fed back to the model as an observation; the loop continues (the model is
     the recovery mechanism).
   - unrecoverable model/gateway error → fail the run.
   - no app-level retries (transport retries already live in the `ai` SDK, mirroring
     Claude Code's `withRetry`).
   - deferred (YAGNI): token/cost budget, wall-clock timeout, parallel tool
     execution, `isReadOnly`/`isConcurrencySafe` metadata, sub-agents.

### Reference: Claude Code orchestration (informing, not copied)

The loop shape mirrors Claude Code's `query.ts` orchestration, adapted to a headless
run + Run Ledger:

- One turn = decide → execute tools → feed results back → repeat (CC `while(true)`
  async generator; ours is a bounded `for` loop).
- Done signal = model emits no tool call. Ours: `{action: "final"}`.
- Tool errors and permission denials are surfaced back to the model as error
  observations, not aborts (CC `toolExecution.ts`: `tool_result` with `is_error`).
- Retries live at the transport layer only (CC `withRetry`); the agent layer does
  not retry.
- F5 deliberately drops CC's `ask` (human-prompt) permission behavior, which exists
  because CC is interactive; F5 keeps only `allow | deny`.

## Module structure

Greenfield unless marked REUSE.

```
src/lib/tools/types.ts      Tool interface, PermissionClass, ToolContext
src/lib/tools/registry.ts   createToolRegistry(): register / get / list
src/lib/tools/echo.ts       echo reference tool (class: read)
src/lib/harness.ts          runAgent() — the ReAct loop
src/app/api/agent/route.ts  thin trigger: POST { question } -> { runId }
REUSE src/lib/model-gateway.ts  callModel()
REUSE src/lib/ledger.ts         startRun/startRunStep/startToolCall/... + getRunTrace
REUSE src/app/runs/[id]/page.tsx run inspector (no changes)
```

## Components

### Tool contract (`src/lib/tools/types.ts`)

```ts
export type PermissionClass =
  | "read" | "enrich" | "reason" | "draft" | "write_internal" | "admin";

export interface ToolContext {
  db: Sql;
  runId: number;
  parentStepId: number; // the tool's own run step, for nested logging
}

export interface Tool<TArgs = unknown, TResult = unknown> {
  name: string;
  description: string;            // surfaced to the model in the prompt
  permissionClass: PermissionClass;
  argsSchema: z.ZodType<TArgs>;   // validated before execute()
  execute(args: TArgs, ctx: ToolContext): Promise<TResult>;
}
```

### Registry (`src/lib/tools/registry.ts`)

A small Map-backed factory. No global singleton — the caller builds a registry and
passes it to `runAgent`, which keeps tests isolated.

```ts
export interface ToolRegistry {
  register(tool: Tool): void;        // throws on duplicate name
  get(name: string): Tool | undefined;
  list(allowed: Set<PermissionClass>): Tool[]; // tools the run may use (for the prompt)
}
export function createToolRegistry(tools?: Tool[]): ToolRegistry;
```

### echo tool (`src/lib/tools/echo.ts`)

```ts
// permissionClass: "read"; argsSchema: { text: string }
// execute: returns { echoed: text }
```

### The loop (`src/lib/harness.ts`)

```ts
const agentDecisionSchema = z.discriminatedUnion("action", [
  z.object({ action: z.literal("tool"), tool: z.string(),
             args: z.record(z.unknown()), thought: z.string().optional() }),
  z.object({ action: z.literal("final"), answer: z.string(),
             thought: z.string().optional() }),
]);

interface RunAgentInput {
  question: string;
  registry: ToolRegistry;
  allowedClasses: Set<PermissionClass>;  // default {read, reason}
  maxSteps?: number;                      // default 8
  provider?: ModelGatewayProvider;        // for tests; default = gateway default
  db?: Sql;                               // default getDb()
}
interface RunAgentResult {
  runId: number;
  status: "succeeded" | "failed";
  answer?: string;
  steps: number;
}
export async function runAgent(input: RunAgentInput): Promise<RunAgentResult>;
```

Flow:

1. `startRun({ runType: "agent_chat", input: { question } })`.
2. `startRunStep({ stepKind: "agent", name: "react_loop" })` — the parent step.
3. For up to `maxSteps` iterations:
   - Build the prompt: system instructions + tool catalog (name, description, args
     JSON schema) restricted to `registry.list(allowedClasses)` + the running
     observation transcript.
   - `callModel({ kind: "generate_object", schema: agentDecisionSchema,
     taskName: "agent_react_decide", promptVersion: "1",
     trace: { runId, parentStepId: agentStep.id }, provider })`
     — the model call is auto-logged by the gateway.
   - `final` → `finishRunStep(agentStep, { output: { answer } })`,
     `finishRun({ output: { answer, steps } })`, return succeeded.
   - `tool` → `executeToolCall(...)`; push `{ decision, observation }` to the
     transcript; continue.
4. Cap reached → `failRunStep(agentStep, ...)`,
   `failRun({ errorType: "max_steps_exceeded" })`, return failed.
5. Unrecoverable `ModelGatewayError` from `callModel` → fail step + run, return failed.

### `executeToolCall` (permission + validation + ledger)

Every attempt opens a tool step + tool call so it appears in the trace. Each branch
returns an observation string that is fed back to the model.

| Case | Ledger record | Observation fed back |
|---|---|---|
| tool not in registry | tool_call failed `unknown_tool` | "Unknown tool: X" |
| class not in allowed-set | tool_call failed `permission_denied` | "Tool X (class Y) is not permitted for this run" |
| args fail Zod parse | tool_call failed `invalid_args` | the Zod error summary |
| `execute` throws | tool_call failed `tool_error` | the error message |
| success | tool_call succeeded + result | the JSON result |

Steps:

```
toolStep = startRunStep({ runId, parentStepId: agentStep.id,
                          stepKind: "tool", name: decision.tool })
toolCall = startToolCall({ runId, runStepId: toolStep.id,
                          toolName: decision.tool, arguments: decision.args,
                          metadata: { permissionClass } })
// on success:  finishToolCall(result) + finishRunStep(output: result)
// on any failure: failToolCall(errorType, message) + failRunStep(...)
```

Permission refusal and invalid args are *not* run failures — they are logged failed
tool calls plus an observation, satisfying "a tool above the run's allowed class is
refused and logged" while letting the model adapt.

## Data flow

```
POST /api/agent { question }
  -> runAgent({ question, registry(echo), allowedClasses:{read,reason} })
       startRun -> startRunStep(agent)
         loop: callModel(generate_object) --(gateway logs model_call)
               -> decision.tool -> executeToolCall
                    startRunStep(tool) -> startToolCall
                      [permission/validate/execute]
                    finish|fail toolCall + step
               -> decision.final -> finishRun
  -> { runId }
GET /runs/[id]  -> getRunTrace -> existing inspector renders the tree
```

## Error handling

- Tool/permission/arg failures: logged as failed tool calls, returned to the model
  as observations, loop continues.
- Model/gateway failure (`ModelGatewayError`): fail the agent step and the run with
  the gateway error code; return `status: "failed"`.
- Max steps: fail the run with `errorType: "max_steps_exceeded"`; the partial trace
  is preserved and inspectable.
- The API route catches and maps any thrown error to a 500 with the run id when one
  exists, so a failed run is still inspectable.

## Testing (TDD, live Postgres — same harness as F3/F4)

Tests reset ledger tables and run migrations in `beforeEach`, and inject a mock
`ModelGatewayProvider` so no real model API is called.

- `tests/harness.test.ts`
  - multi-step happy path: provider returns one `tool` decision then `final`; assert
    the trace has an agent step with a tool child, a recorded tool_call (succeeded),
    model_calls present, run `succeeded`, and the answer returned. (Acceptance #1, #3)
  - permission refusal: register a higher-class tool; allowed-set excludes it;
    provider calls it, then finals. Assert a failed tool_call with
    `permission_denied` exists in the trace and the run still succeeds. (Acceptance #2)
  - max steps: provider always returns a `tool` decision; assert the run fails with
    `max_steps_exceeded` after `maxSteps` iterations.
  - tool error: tool `execute` throws; assert failed tool_call `tool_error`, then the
    model finals and the run succeeds.
  - invalid args: provider returns args failing the schema; assert failed tool_call
    `invalid_args`, then recovery.
- `tests/tool-registry.test.ts`: register/get, duplicate-name throws,
  list filters by allowed classes.
- `tests/echo-tool.test.ts`: echo returns its input.

## Acceptance criteria (from the task breakdown)

- [ ] A run executes a multi-step loop that calls the model via the gateway and at
      least one registered tool.
- [ ] Tool registry enforces permission classes (a tool above the run's allowed class
      is refused and logged).
- [ ] The full run (steps, tool calls, model calls, status) appears in the run
      inspector.

## Maps to plan tasks

- F5.1 ReAct loop → `src/lib/harness.ts` (§ The loop)
- F5.2 Tool registry + permission classes + enforcement → `src/lib/tools/*` + `executeToolCall`
- F5.3 `echo` tool + end-to-end ledger wiring → `src/lib/tools/echo.ts` + `POST /api/agent` + inspector
