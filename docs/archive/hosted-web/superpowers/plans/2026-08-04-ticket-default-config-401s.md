# Ticket — The shipped default provider config fails, and fails silently

Date: 2026-08-04
Found by: fresh `npm run dev` on `main` with the repo's own `.env.example` defaults
Est: 0.25

## Problem

`.env.example` ships:

```
SOURCECADO_GENERATION_PROVIDER=anthropic
SOURCECADO_GENERATION_MODEL=claude-sonnet-4-6
```

Every chat run on that configuration dies immediately:

```
status: failed
[model error: 401 {"type":"error","error":{"type":"authentication_error",
                   "message":"API key is invalid."}}]
```

0.3 seconds, zero tool calls, and — because of the defect in
`2026-08-04-ticket-loop-step-budget-partial-answer.md` — **`answer` is
`undefined`**, so the user sees an empty bubble with a small grey "failed" and no
explanation. Nothing on screen says "authentication."

## Two separate faults

**1. The default points at a provider whose key does not work.** The
`ANTHROPIC_API_KEY` in `.env.local` is 108 characters and 401s. A real key is
`sk-ant-api03…`; 108 characters matches an OAuth token, which the gateway cannot
use. This is already recorded in project memory as a known gotcha — which means
it has cost time more than once.

**2. A provider auth failure is indistinguishable from a bad answer.** The 401 is
a configuration error, not a model error, but it surfaces through the same
`stopReason: "error"` path as any other provider failure and renders as an empty
answer.

## Provider state, verified 2026-08-04

| Provider | Key | Generation | Notes |
|---|---|---|---|
| anthropic | 108 chars | **401** | Supported on both paths, but the key is invalid |
| openai | 164 chars, `sk-proj-` | valid — `HTTP 200`, `gpt-4o` available | **Streaming only** — see `2026-08-04-ticket-gateway-provider-inconsistency.md` |
| deepseek | 35 chars | valid — `HTTP 200` | Supported on both paths. Models are `deepseek-v4-flash` and `deepseek-v4-pro` only |

**`deepseek` is currently the only provider that both works and is supported
everywhere.** Note `deepseek-chat` is a legacy alias; the live model list is just
the two v4 ids.

## Fix

1. **Change the `.env.example` default to a configuration that works.** Given the
   table above, `deepseek` with `deepseek-v4-flash`. Revisit if the Anthropic key
   is replaced with a real API key.

2. **Fail loudly on provider auth errors.** A 401/403 from a provider should not
   render as an empty answer. Surface it as a configuration problem naming the
   env var to fix — the same treatment `config_error` already gets — instead of
   collapsing into the generic error path.

3. **Correct the stale comment in `.env.example`**, which still says the provider
   defaults to deepseek "when unset, so set this to anthropic if you only have an
   `ANTHROPIC_API_KEY`." That advice now produces a broken setup.

## Notes for implementation

- Do not commit key material. Only the provider/model defaults and the comment
  change; keys stay in `.env.local`, which is gitignored.
- Point 2 overlaps `2026-08-04-ticket-loop-step-budget-partial-answer.md`, which
  is already widening how non-`succeeded` results reach the user. Coordinate:
  that ticket adds a `truncated` status, this one needs auth failures to stay
  clearly *failed* but become **explicable**.
- `deepseek-v4-flash` is a reasoning model — it returns `reasoning_content`
  alongside `content`. `src/lib/llm/openai-compat.ts:121` reads only
  `delta.content`, which is correct and matches the design doc's stance that raw
  chain-of-thought is not a product artifact. No change needed; recorded so the
  next person does not "fix" it.

## Done when

- A clean checkout following `.env.example` produces a working chat on the first
  run.
- A provider auth failure renders a message naming the misconfigured variable,
  not an empty answer bubble.
