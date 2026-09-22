# Onboarding backlog reconciliation

Later scope decision: Fisher reduced this inventory to eight active issues and
deferred or superseded the others. See [the current backlog](../BACKLOG.md) and
[GitHub #79](https://github.com/FisherXZ/Sourcecado/issues/79). The findings below
remain the historical review; their issue-state descriptions predate cleanup.

Date: 2026-09-21. Baseline: `origin/main` at
`614431aef8ec139751ed85b211d7eae31358f708`. GitHub inventory: **38 open issues,
no open PRs** when this pass began. This is a dated reconciliation record;
[TODOS.md](../BACKLOG.md) is the living product summary. GitHub issue bodies,
labels and states were read but not changed in this pass.

The scope was to verify and fix clean-checkout setup, add a contributor guide,
review architecture and correctness independently, and update living docs.
The reviews identify subsequent work; this pass does not implement every
product issue. Existing user deferrals remain in force.

## What changed during this pass

| Setup problem | Evidence before change | Change / verification |
| --- | --- | --- |
| An arbitrary `python3` is used despite the 3.14 lockfile | Plain `make setup` created a Python 3.8 environment and failed installing a locked dependency | Makefile defaults to `python3.14`; a new venv and plain `make setup` succeeded. `PYTHON` remains overridable. |
| First config copy assumes its parent exists | Copy into a fresh profile-like directory returned “No such file or directory” | Guides create the directory and preserve an existing `.env`; creation and non-overwrite cases checked. |
| Browser auth ignores isolated backend state | Three regression tests failed: custom path, home-relative path, and missing isolated token | Vite follows `CLUB_STATE_DIR`; all six token tests pass, including explicit-token precedence and no token in production builds. |
| Native development requires a release resource absent from checkout | `cargo check --locked` failed on `resources/sourcecado-backend` | `tauri:dev` supplies a development resource override. `make native` built and launched, with API 200 responses and accepted chat WebSockets. Release config still requires the frozen backend. |
| Packaging independently selects `python3` | `build_backend.sh` ignored the Makefile interpreter selection | Packaging now receives `PYTHON`, defaulting to 3.14 for direct use too. See verification receipt below. |

The [onboarding guide](../../CONTRIBUTING.md) now supplies a reading path, isolated
state setup, credential-free expectations, native/browser distinctions,
troubleshooting, focused test commands and contribution expectations. Living
subsystem docs acknowledge the unresolved approval, restart and evidence gaps.

## Recommended next work

1. **Unify reviewed authority for send/enrich (#165).** Both current paths ask
   permission, but generic chat tools lack the same recipient, reviewed-body,
   matching and provenance guarantees. Require the same authority and receipts
   from every callable entry point.
2. **Finish safe restart continuation (#128).** Deliver persisted final answers
   and resume eligible runs under their original identity. Preserve quarantine
   for uncertain effects. Track person-receipt repair explicitly rather than
   assuming run continuation solves every partial write.
3. **Make sequence completion truthful (#184), then verify refresh (#169).**
   A director should deliberately choose an outcome; reopening should not retain
   a fabricated `closed` outcome. Use the same UI action to test all projections.
4. **Wire the missing quality checks (#166).** Establish fresh baselines rather
   than treating August's coverage/lint numbers as current. TypeScript checking
   already runs in CI. The PR template is already tracked.

The living-brief defects (#131–#133) remain real. #134 explicitly records
Fisher's deferral; this review does not silently promote them into release or
contributor-onboarding gates. Documenting known gaps lets a teammate contribute
without implying the product is finished.

## Disposition of all 38 open issues

**Confirmed** means source tracing and/or a reproduction supports the remaining
work; consult the linked review for the evidence level. **Carry forward** means
the issue was inventoried but its complete acceptance criteria were not
reverified. It is not a new defect finding. None of these rows claims that an
external issue was closed.

| Issue | Disposition | Next action / correction |
| --- | --- | --- |
| [#79](https://github.com/FisherXZ/Sourcecado/issues/79) release program | Keep as umbrella; stale checklist | Reconcile child completion with receipts. #84 is closed; #78 is closed **not planned**, which is not signed-preview certification. Use #134 for remaining human acceptance. |
| [#127](https://github.com/FisherXZ/Sourcecado/issues/127) kept candidates on Board | Superseded product framing; closure candidate | Current spec explicitly keeps candidates durable but excludes them from Contacts until they have a sequence. Retire the fourth-column proposal against that decision. A separate kept-person discovery surface would need new scope. |
| [#128](https://github.com/FisherXZ/Sourcecado/issues/128) restart continuation | Confirmed, priority | Startup computes but does not consume RESUME/DELIVER. Update its stale reference to #63 remaining open; #63 is closed. |
| [#131](https://github.com/FisherXZ/Sourcecado/issues/131) disappearing source failure | Confirmed; existing deferral preserved | Persist/derive failure evidence beyond the transient refresh response. |
| [#132](https://github.com/FisherXZ/Sourcecado/issues/132) raw reply as wants | Confirmed; existing deferral preserved | Distinguish an actual request from greetings, quoting and uncertain evidence. |
| [#133](https://github.com/FisherXZ/Sourcecado/issues/133) unassigned reply gap | Confirmed by source; existing deferral preserved | Close the matching gap when a reply is successfully filed; verify unrelated gaps remain. |
| [#134](https://github.com/FisherXZ/Sourcecado/issues/134) human release QA | Keep; reconcile stale prerequisites | #130/#136 are closed, #138 merged, and the preview trust key exists. Secret provisioning and complete artifact acceptance were not checked. Preserve unsigned Track 1 and deferred Apple Track 2. |
| [#144](https://github.com/FisherXZ/Sourcecado/issues/144) observation autonomy decision | Decision recorded in issue; docs work remains | Land its ADR and precedence-consistent guardrails/spec amendment together. This setup pass does not change autonomy policy. |
| [#145](https://github.com/FisherXZ/Sourcecado/issues/145) time-based follow-up | Source supports remaining gap | Current projection handles reply reasons, not waiting-too-long. Retain the issue's 4-day/2-day policy and dependency on #144. |
| [#146](https://github.com/FisherXZ/Sourcecado/issues/146) reply heartbeat | Carry forward | Depends on #144/#145. Keep observations separate from spend/send; prove restart/catch-up behavior when implemented. |
| [#163](https://github.com/FisherXZ/Sourcecado/issues/163) Scheduled lifecycle | Keep; prerequisite resolved | #162 is closed and #194 delivered detail view. Detail display is not persistent editing, pause/resume or deletion. |
| [#165](https://github.com/FisherXZ/Sourcecado/issues/165) send/enrich implementations | Confirmed, priority | Unify identity/version/receipt guarantees; do not describe the gap as absence of approval. |
| [#166](https://github.com/FisherXZ/Sourcecado/issues/166) quality gates | Confirmed in part; narrow stale claims | Python typing, lint and coverage gates remain; GUI lint/coverage and npm audit remain. TypeScript already runs via `make build`; PR template is tracked. Refresh dependency findings. |
| [#167](https://github.com/FisherXZ/Sourcecado/issues/167) large server/client modules | Confirmed concentration | Extract the approval service alongside #165; avoid a file-size-driven rewrite. |
| [#168](https://github.com/FisherXZ/Sourcecado/issues/168) Club identifiers | Still present; compatibility work | Document current names now; migration, backup and rollback are prerequisites to renaming stored state. |
| [#169](https://github.com/FisherXZ/Sourcecado/issues/169) Contacts refresh | Confirmed by source | Person File refreshes itself after a sequence change but does not emit the event Contacts listens for. Test the real transition and navigation path instead of manually dispatching the event. |
| [#170](https://github.com/FisherXZ/Sourcecado/issues/170) tool registry | Partially valid; narrow claim | Policy validation already exists in effective_tools. Investigate missing dispatch/availability/filing agreement, then consolidate useful metadata. |
| [#171](https://github.com/FisherXZ/Sourcecado/issues/171) Anthropic caching | Carry forward; not a setup blocker | No outgoing `cache_control` found in provider.py. Verify actual provider semantics and measured benefit when implementing. |
| [#172](https://github.com/FisherXZ/Sourcecado/issues/172) durable search index | Carry forward | Feature proposal, not verified missing acceptance. No implementation in this pass. |
| [#173](https://github.com/FisherXZ/Sourcecado/issues/173) mid-turn steering | Carry forward | Keep as a separate behavior change with checkpoint/queue tests. |
| [#174](https://github.com/FisherXZ/Sourcecado/issues/174) global output truncation | Carry forward | Define overflow retention, provenance and model-visible behavior before implementation. |
| [#175](https://github.com/FisherXZ/Sourcecado/issues/175) Keychain secrets | Current file-backed storage confirmed | Keep migration/fallback work separate. Mode-0600 JSON is not Keychain storage. |
| [#176](https://github.com/FisherXZ/Sourcecado/issues/176) database connections | Confirmed; correct wording | Drive explicitly selects DELETE journaling. Establish intentional defaults/exceptions; do not blindly switch every store to WAL. |
| [#177](https://github.com/FisherXZ/Sourcecado/issues/177) structured tool failures | Carry forward | Review error contracts end to end when implemented; no closure evidence here. |
| [#179](https://github.com/FisherXZ/Sourcecado/issues/179) parallel tools/truncation | Carry forward | Separate response-truncation correctness from concurrency optimization; preserve approval and mutation ordering. |
| [#180](https://github.com/FisherXZ/Sourcecado/issues/180) unavailable backend | Partly addressed | Boot has bounded retries and a Retry control. Review remaining raw errors and behavior across surfaces. |
| [#181](https://github.com/FisherXZ/Sourcecado/issues/181) connection health labels | Partly addressed | Both surfaces use the same endpoint but translate `available` differently; token presence is not live verification. |
| [#182](https://github.com/FisherXZ/Sourcecado/issues/182) failed-turn recovery | Mostly confirmed | Terminal provider failures still lack the same actionable controls as tool failures. Provider-verification staleness needs reproduction. |
| [#183](https://github.com/FisherXZ/Sourcecado/issues/183) command palette | Carry forward | Reproduce current navigation, focus and people-search acceptance before editing. |
| [#184](https://github.com/FisherXZ/Sourcecado/issues/184) Done semantics | Confirmed, priority | Require deliberate completion and truthful outcome/reopen behavior. |
| [#185](https://github.com/FisherXZ/Sourcecado/issues/185) person-file presentation | Carry forward | Design improvement; coordinate with outcome semantics and evidence truthfulness. |
| [#188](https://github.com/FisherXZ/Sourcecado/issues/188) chat chrome | Carry forward | Recheck against recent composer/chat changes before repeating the original observations. |
| [#189](https://github.com/FisherXZ/Sourcecado/issues/189) Board density/actions | Partly superseded by #164 | Contacts is now a table with state filters and search. Rewrite acceptance around current Contacts; reassess remaining density, sort and row actions rather than implementing three-column cards. |
| [#190](https://github.com/FisherXZ/Sourcecado/issues/190) fonts | Carry forward | Recheck actual bundled fonts and numeric rendering. |
| [#191](https://github.com/FisherXZ/Sourcecado/issues/191) spacing/buttons | Carry forward | Scope shared styles around concrete repeated behavior; no blanket redesign required for onboarding. |
| [#192](https://github.com/FisherXZ/Sourcecado/issues/192) dark mode | Carry forward | Verify preference persistence and native chrome together when addressed. |
| [#193](https://github.com/FisherXZ/Sourcecado/issues/193) Settings diagnostics | Carry forward | Keep as a bounded Settings UX improvement. |
| [#197](https://github.com/FisherXZ/Sourcecado/issues/197) old error banner | Clarify desired behavior | Preserve failed-turn history; distinguish an old failure receipt from a current connection/provider problem. |

## Review evidence

- [Architecture review](2026-09-21-onboarding-architecture-review.md): Astra;
  system map, five findings, 61 focused tests, distinctions between structural
  proposals and confirmed behavior.
- [Correctness review](2026-09-21-onboarding-correctness-review.md): GPT-5.6 Sol;
  three prioritized findings, bounded reproductions/source tracing, 97 focused
  backend and 108 focused GUI tests.
- [Setup verification](2026-09-21-onboarding-setup-verification.md): commands,
  observed failures, fixes, full test totals and verification limits.

Older audits stay dated. Their numbers and line references are historical;
their findings are not automatically an instruction to implement everything.
This record accounts for every open issue while keeping unverified items
explicitly distinct from fresh findings.
