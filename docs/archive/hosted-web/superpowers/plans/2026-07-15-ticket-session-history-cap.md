# Ticket: Cap resumed-session history before it outgrows the context window

Date: 2026-07-15 · Status: TICKETED (accepted-for-v1 in PR #16 review).
Priority: MUST-FIX before real team use. Blocked by: nothing (post-R9 fine).

## Problem

R6 threads `priorMessages` (the full stored transcript) into the loop
UNCAPPED (harness.ts:107), and `getOrCreateLatestSession` resumes the same
session indefinitely. A long-lived session eventually exceeds the provider
context window → every turn 400s permanently; the only user escape is "New
chat". Found in PR #16 principal review (finding #4).

## Fix shape (design constraint, not naive truncation)

Cut history at a USER-message boundary only — never split an assistant
tool_use from its tool_result message (providers reject unpaired blocks; see
agent-loop.ts transcript-repair logic for the pairing invariant). Options:
keep-last-N-exchanges with N sized by rough token budget, or summarize-then-
drop older exchanges (bigger; needs design). Either way: regression test that
a resumed over-cap session still produces a provider-valid request.

## Related (same follow-up window)

- Per-run cost control for enrich tools (Fisher, 2026-07-15 — "urgent").
- capture/redaction gating wired through callModel + streamAgentTurn +
  chat_messages in one ticket once a redaction policy exists (PR #16 review
  adjudication).

## R7 security residuals (added 2026-07-21, from PR #18 principal review)

Fold into the URGENT post-R9 enrich-hardening follow-up (alongside per-run
cost control):
1. **web_fetch DNS-rebinding TOCTOU.** Per-hop redirect re-validation +15s
   timeout are in place and close the redirect-to-internal vector, but NOT
   same-host rebinding. Airtight fix = undici IP-pinning (a new dependency =
   runbook stop-condition, deliberately deferred). Single-tenant v1 accepted.
2. **web_fetch post-buffer size cap.** `res.text()` buffers the whole body
   before the 500k char slice, so the cap doesn't bound memory. Dep-free fix:
   stream res.body, abort at byte limit. Lower likelihood at v1 volume.
3. **(dropped, not ticketed)** add_memory_note run/actor stamp: provenance
   already exists via tool_calls.run_id + arguments_json; a
   source_records.created_by_run_id migration is only worth it if a join-free
   lookup is later wanted. Not doing it now.

Note: the IPv4-mapped-IPv6 hextet SSRF gap the review found IS fixed on main
(captured in the #18 squash merge).
