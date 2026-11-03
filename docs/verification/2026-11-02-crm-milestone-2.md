# CRM milestone 2 verification — November 2, 2026

Status: verified locally on the integrated branch. This record does not certify release acceptance (#212).

## Scope

Milestone 2 stacks on the milestone 1 integration (#294). It adds:

- Shared once-only approvals for send and enrichment (#165), with identity rechecks after live review.
- Deliberate one-person enrichment review with saved outcomes (#240).
- An optional person-bound reminder after a confirmed send (#241).
- Protected person corrections with duplicate review (#243). Schema 10 adds identity revisions, so a correction invalidates older send and lookup approvals.

## Changes made while integrating

- Two Codex workers were stopped mid-change. Their work was ported onto one branch and finished.
- An older enrichment attempt with an unknown outcome stays unresolved until a new review names it. A newer result does not hide it.
- A provider identity mismatch shows a safe explanation. Raw provider text is not shown.
- "Review person details" appears only for a real identity problem, not after every successful lookup.
- Apollo receipts are replay-safe. A retried completion returns the filed receipt and does not reapply facts over a later human correction. The same approval id with different provider facts is refused.
- `styles.css` imports the reminder and enrichment stylesheets. The sourcing eval fixture records the new source fields.

## Automated results

| Check | Result |
| --- | --- |
| Python 3.14 backend, hash-locked environment | 2,907 passed, 3 skipped |
| Node frontend (Vitest) | see PR description for the final count |
| TypeScript | passed |
| Sourcing eval scenes | 15/15 inside the backend suite |

## Browser QA

The real React and FastAPI app ran against fictional people. Gmail was `FakeGmail`. Apollo was a local stub that returns a canned person. No real mail was sent and no credits were spent.

| Flow | Observed |
| --- | --- |
| Keyboard finder opens the focused person | Pass |
| Enrichment review shows person, match basis and unknown cost | Pass |
| Approve once → contact details found, actual credits 1, updated fields listed | Pass |
| Correction review shows saved vs corrected value and approval warning | Pass |
| Duplicate person (matching email) blocks save until the director confirms | Pass |
| Saved correction updates details and the enrichment panel | Pass |
| Draft → request approval → allow once → sent | Pass |
| After-send reminder needs an explicit date, saves a task, never sends | Pass |
| Contacts shows the person as Open with the reminder as next action | Pass |
| Person File at 390 px: no horizontal overflow, no console errors | Pass |

Evidence: [evidence/2026-11-02-crm-m2](evidence/2026-11-02-crm-m2/).

## Known gaps, not in this milestone

- Contacts lists only people with an active sequence. Saved people before outreach do not appear.
- The sent-message card shows raw Gmail message and thread ids.
- The outreach To/Subject layout is misaligned.
- The agent cannot read change history, mail threads or enrichment status, and cannot set an after-send reminder.
- MCP reaches the agent only as Granola `list_meetings`.

These are milestone 3 to 5 work.
