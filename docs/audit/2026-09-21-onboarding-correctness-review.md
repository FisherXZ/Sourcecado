# Sourcecado onboarding correctness review

Date: 2026-09-21
Reviewed commit: `614431aef8ec139751ed85b211d7eae31358f708` (`origin/main`)
Scope: bounded review of the active `desktop/` runtime, focused on approval/effect safety, person isolation, restart continuation, sequence transitions, degraded UI states, connector status, and the living brief. This is not a full repository audit.

## Verdict

The effect fence and person-binding paths have substantial focused coverage, and I did not find a reproducible duplicate send/enrich or cross-person projection defect in this pass. Three product-correctness failures deserve the highest backlog priority from this review. They are product behavior findings, not contributor-environment onboarding gates; the separate setup review determines whether a new contributor can install, build, and test the repository.

1. **P1 — Restart classifies recoverable runs but never resumes or delivers them (#128).** `create_app` runs the restart planner once, stores its return value in `app.state.run_restart`, and explicitly says resuming/delivering are not done there (`desktop/coworker/server.py:462-468`). `restart()` only quarantines ambiguous effects and returns all verdicts (`desktop/coworker/agent_run_resume.py:245-270`). Its own test proves a post-generation cut becomes `DELIVER`, but only asserts the verdict (`desktop/tests/test_agent_run_restart.py:333-352`); no production consumer acts on it. A crash during ordinary model/read/approval work therefore strands the turn, and a crash after terminal generation withholds the recorded answer.

2. **P1 — One click on Done fabricates a durable outcome that survives reopening (#184).** `PersonFile.move()` immediately calls `setPersonSequence(personId, state)` with no confirmation, outcome choice, or undo (`desktop/surfaces/gui/src/PersonFile.tsx:197-209`). The store substitutes `"closed"` when a director selects Done without an outcome and otherwise carries the prior outcome through every state change (`desktop/coworker/people.py:1024-1037`). The brief independently renders any stored outcome (`desktop/coworker/brief.py:375-403`), so reopening produces an active sequence alongside `Outcome: closed`. Existing UI coverage verifies only the direct one-click transition (`desktop/surfaces/gui/tests/boardPerson.test.tsx:411-426`) and does not exercise Done/reopen semantics.

3. **P1 — The living brief converts any inbound snippet into a claim about what the person wants (#132).** `_wants()` selects the newest inbound `snippet` (or event summary) without stripping quoted text, decoding entities, or checking whether it expresses a want, then prefixes it with `What they want:` (`desktop/coworker/brief.py:598-617`). Thus a reply such as `hi` becomes a positive want claim, and a quoted outbound body can be attributed to the recipient. This is worse than preserving the explicit missing-information gap because it creates a misleading handoff record.

## Reproduction and verification

Focused backend tests:

```text
$ desktop/.venv/bin/python -m pytest -q \
    desktop/tests/test_agent_run_restart.py \
    desktop/tests/test_people.py \
    desktop/tests/test_living_brief.py \
    desktop/tests/test_reply_filing.py
97 passed, 1 warning in 2.05s
```

These passing tests establish that the current assertions accept the defects above; notably restart tests stop at verdict classification and living-brief tests cover a meaningful inbound request, not low-confidence or quoted replies.

Focused GUI tests:

```text
$ npm test -- --run tests/boardPerson.test.tsx tests/personFileBrief.test.tsx \
    tests/chatPage.test.tsx tests/connectionsPage.test.tsx tests/settingsPage.test.tsx
5 files passed, 108 tests passed in 5.35s
```

Two deterministic reproductions were executed against an isolated store under `/tmp`:

```text
$ PYTHONPATH=desktop desktop/.venv/bin/python /tmp/sourcecado_correctness_repro.py /tmp/sourcecado-correctness-repro-exact
SEQUENCE_REPRO={"after_done": {"outcome": "closed", "sequence_state": "done"}, "after_open": {"outcome": "closed", "sequence_state": "open"}}
WANTS_REPRO={"state": "current", "text": "What they want: hi On Fri, operator &lt;operator@example.test&gt; wrote: dinner invite"}
```

The `/tmp/sourcecado_correctness_repro.py` scratch script created a new person with no outcome, called `set_sequence(..., "done", actor="director")`, then `set_sequence(..., "open", actor="director")`. It then inserted a fake Gmail inbound event and rendered `brief_payload(person_brief(...))`. It used no connector and no personal state.

#128 remains source-traced rather than dynamically reproduced end to end. The existing restart test executes its persisted `delivery` cut and observes `ResumeAction.DELIVER`, while production startup only stores the returned verdict list. I did not claim that a second process successfully delivered or resumed the run; no production consumer exists to perform that action.

## Backlog reconciliation

| Issue | Disposition at reviewed commit | Evidence |
|---|---|---|
| #128 restart continuation | **Open; confirmed P1.** | Main finding 1. `RESUME` and `DELIVER` are computed and dropped. |
| #184 sequence completion | **Open; confirmed P1.** | Main finding 2. Done invents `closed`; reopening retains it. |
| #169 Board refresh after Person File transition | **Open; confirmed.** | `PersonFile.move()` refetches only its own file and does not dispatch `sourcecado:board-changed` (`PersonFile.tsx:197-209`). The Board listens for that event; the test dispatches it manually rather than through `move()` (`boardPerson.test.tsx:428-433`). |
| #197 stale error banner | **Open; behavior confirmed, severity/product intent needs refinement.** | Terminal failure notices are durable transcript messages. A later successful turn does not remove the earlier failed turn. That preserves history, but the UI does not visually bind the failure to its old turn, producing the reported stale-banner impression. Do not simply delete audit history; scope the receipt to its turn or mark it resolved. |
| #182 failed turn recovery | **Open; mostly confirmed.** | Terminal provider notices render prose only in `AssistantMessage` (`AssistantMessage.tsx:135-168`): no retry or Settings link. Generic notice copy says “Model provider” and does not name the active provider (`AssistantMessage.tsx:45-76`). Tool-step failures do have recovery actions, but that does not cover a terminal model failure. Provider verification staleness was not dynamically reproduced. |
| #180 unavailable backend | **Partially addressed; keep open.** | Boot retries are bounded (100/300/900 ms) and a Retry control exists, so endless skeletons are fixed. However `AppShell` stores and renders the raw exception message (`AppShell.tsx:324-325`), and the regression test explicitly expects `backend offline` (`shell.test.tsx:917-934`). A full-surface plain-language disconnected state and toast suppression were not demonstrated. |
| #181 connector status | **Partially addressed; keep open.** | Connections now has details and Connect/Fix actions. Both surfaces consume `/v1/connectors`, but their labels still disagree: `available` renders “Available” on Connections and “Needs attention” in Settings (`ConnectionsPage.tsx:28-35`; `SettingsPage.tsx:51-55`). Backend status derives from token/config presence, not a live probe, and exposes no last-verified time (`server.py:1665-1850`). |
| #131 partial-source persistence | **Open; confirmed.** | `_failed_sources()` only reads the transient `refresh` argument (`brief.py:693-705`). Ordinary person reads call the projection without that refresh result, so the warning cannot survive navigation. |
| #132 raw reply as wants | **Open; confirmed P1.** | Main finding 3. |
| #133 resolved unassigned-reply gap | **Open; confirmed by source.** | Ambiguous filing records `unassigned_reply` gaps (`reply_filing.py:397-406`); successful filing calls `file_inbound_reply` (`reply_filing.py:383-396`) and contains no matching gap-close operation. |

## Safety paths checked

- Approval/effect fencing has dedicated restart, stale-approval, approved-send, approved-enrichment, effect-fence, race, and ambiguous-outcome suites. The focused restart suite passed, and source ordering records dispatch before the external call and outcome afterward. No new route that intentionally replays an ambiguous external effect was found.
- Living-brief projection rejects records whose `person_id` differs from the projection identity (`brief.py:708-719`), and person-bound route loading checks the returned person identity before replacing the thread (`ChatPage.tsx:145-175`). Existing person-bound recovery and API tests cover mismatch rejection. No cross-person rendering reproduction was found.
- The architecture audit owns the broader duplicate send/enrich path inventory; this review only reports the bounded correctness result above.

## Verification limits

No live Gmail send, Apollo enrichment, OAuth flow, or personal state was used. I did not run a process-kill integration harness that actually boots a second sidecar and delivers/resumes a run, because production has no delivery/resume executor to exercise. I did not perform browser-level timing tests for sidecar death or stale provider verification. The test runs used the fresh local Python and GUI dependencies installed in this worktree and temporary pytest state.
