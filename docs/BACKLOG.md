# Active and deferred backlog

Decision: 2026-09-21. Fisher approved a focused active backlog and then removed
the proposed Done/reopen and living-brief tickets. Eight issues remain active.
The earlier [38-issue review](audit/2026-09-21-onboarding-backlog.md) records the
findings before this scope decision.

## Active work

The order below helps a teammate start with small changes. It is not a severity
ranking; the approval and recovery work needs an experienced owner or pairing.

| Order | Issue | Size | Dependency |
| --- | --- | --- | --- |
| 1 | [#169: Update Contacts after changing a person's status](https://github.com/FisherXZ/Sourcecado/issues/169) | Small | None |
| 2 | [#197: Show old chat errors as part of the turn that failed](https://github.com/FisherXZ/Sourcecado/issues/197) | Small | None |
| 3 | [#181: Use the same connection labels in Connections and Settings](https://github.com/FisherXZ/Sourcecado/issues/181) | Small | None |
| 4 | [#176: Use a shared, tested way to open SQLite databases](https://github.com/FisherXZ/Sourcecado/issues/176) | Medium | None |
| 5 | [#165: Use the same approval checks for chat and person-file actions](https://github.com/FisherXZ/Sourcecado/issues/165) | Large / experienced owner | None |
| 6 | [#128: Finish interrupted runs safely after restart](https://github.com/FisherXZ/Sourcecado/issues/128) | Large / experienced owner | [#165](https://github.com/FisherXZ/Sourcecado/issues/165) |
| 7 | [#170: Catch missing tool setup before a tool can run](https://github.com/FisherXZ/Sourcecado/issues/170) | Medium | None |
| 8 | [#163: Add edit, pause, resume and delete controls to Scheduled tasks](https://github.com/FisherXZ/Sourcecado/issues/163) | Large feature | None |

The first three carry the `good first issue` label. Implementation uses the
existing fake connectors and local test suites; normal teammate review still
applies. The architecture findings are in #165 (shared approval service), #128
(recovery and receipt repair), #176 (database connections), and #170 (tool checks).
The rejected Done/reopen and living-brief findings remain recorded below.

## Deferred work

GitHub issue [#79](https://github.com/FisherXZ/Sourcecado/issues/79) preserves the
active list, links to deferred work and the original release program. Deferred
issues are closed as **not planned for this active backlog**, not completed.
Their original descriptions and comments remain available for reopening.

| Issue | Disposition |
| --- | --- |
| [#79: P0: Ship the Sourcecado sourcing OS release program](https://github.com/FisherXZ/Sourcecado/issues/79) | Closed as the archive for deferred work and earlier release planning. The eight active tickets and the deferred-work index are recorded in the updated body. This is backlog organization, not release completion. |
| [#127: dont Kept candidates are invisible on the Board until first contact](https://github.com/FisherXZ/Sourcecado/issues/127) | Superseded by the current product decision: Contacts contains active sequences. Kept people remain durable Person Files before outreach; this issue is not a request to add a fourth sequence state. |
| [#131: Living brief reports a failed source only once, then looks complete](https://github.com/FisherXZ/Sourcecado/issues/131) | Removed from the active backlog at Fisher's request. The Done/reopen and living-brief tickets were dropped from the proposed onboarding set. This issue remains a record of known behavior, not a completed fix. |
| [#132: Living brief reports the raw quoted reply as what the person wants](https://github.com/FisherXZ/Sourcecado/issues/132) | Removed from the active backlog at Fisher's request. The Done/reopen and living-brief tickets were dropped from the proposed onboarding set. This issue remains a record of known behavior, not a completed fix. |
| [#133: An unassigned-reply gap stays open after the reply is filed](https://github.com/FisherXZ/Sourcecado/issues/133) | Removed from the active backlog at Fisher's request. The Done/reopen and living-brief tickets were dropped from the proposed onboarding set. This issue remains a record of known behavior, not a completed fix. |
| [#134: P0: HITL release QA checklist (Track 1 unsigned, Track 2 signing deferred)](https://github.com/FisherXZ/Sourcecado/issues/134) | Deferred from the active teammate backlog. The release acceptance checklist and its prior decisions remain here. Closing this issue does not certify install, update, rollback, signing or the live connector journey. |
| [#144: Decision: observation is autonomous, spending and sending stay human-gated](https://github.com/FisherXZ/Sourcecado/issues/144) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#145: Motion policy: follow_up needs a time axis](https://github.com/FisherXZ/Sourcecado/issues/145) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#146: Heartbeat: background reply refresh, motion recompute, auto-advance on reply](https://github.com/FisherXZ/Sourcecado/issues/146) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#166: P2: wire lint, type, coverage, and npm-audit gates into CI while the baselines are near zero](https://github.com/FisherXZ/Sourcecado/issues/166) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#167: P2: decompose the two god seams: server.py (63 routes, 3,687 lines) and api.ts (2,214 lines)](https://github.com/FisherXZ/Sourcecado/issues/167) | The send/enrich service extraction is now part of #165. Broader router, client and store refactoring is deferred until a concrete change needs it. |
| [#168: P3: finish the club-to-sourcecado rename in load-bearing state before migration cost compounds](https://github.com/FisherXZ/Sourcecado/issues/168) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#171: P1: Opt into Anthropic prompt caching with cache_control breakpoints](https://github.com/FisherXZ/Sourcecado/issues/171) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#172: P2: Derived full-text search index over durable stores](https://github.com/FisherXZ/Sourcecado/issues/172) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#173: P2: Mid-turn checkpoint steering from the chat queue](https://github.com/FisherXZ/Sourcecado/issues/173) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#174: P2: Global tool-output truncation layer with full-output overflow files](https://github.com/FisherXZ/Sourcecado/issues/174) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#175: P2: Move secrets at rest to the macOS Keychain](https://github.com/FisherXZ/Sourcecado/issues/175) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#177: P2: Carry structured failure kinds through tool results](https://github.com/FisherXZ/Sourcecado/issues/177) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#179: P2: Parallel tool dispatch with truncated-response batch poisoning](https://github.com/FisherXZ/Sourcecado/issues/179) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#180: P1: Unreachable sidecar renders as endless skeletons and a raw 401 toast](https://github.com/FisherXZ/Sourcecado/issues/180) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#182: P1: A failed turn is a dead end. Add retry, name the provider, link the fix](https://github.com/FisherXZ/Sourcecado/issues/182) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#183: P1: Command palette cannot find people, survives navigation, and does not trap focus](https://github.com/FisherXZ/Sourcecado/issues/183) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#184: P1: One unconfirmed click on Done closes a person and fabricates the outcome](https://github.com/FisherXZ/Sourcecado/issues/184) | Removed from the active backlog at Fisher's request. The Done/reopen and living-brief tickets were dropped from the proposed onboarding set. This issue remains a record of known behavior, not a completed fix. |
| [#185: P1: Person file reads as a database dump. Collapse, reorder, remove internals](https://github.com/FisherXZ/Sourcecado/issues/185) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#188: P2: Chat chrome shows placeholder telemetry and stale conversation titles](https://github.com/FisherXZ/Sourcecado/issues/188) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#189: P2: Board rows are 64px against the 36-38px spec, with no actions, sort, or filters](https://github.com/FisherXZ/Sourcecado/issues/189) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#190: P2: Geist Mono never renders, tabular-nums is missing, and brand fonts are CDN-only](https://github.com/FisherXZ/Sourcecado/issues/190) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#191: P2: Spacing and radius are magic numbers and every file hand-rolls its own buttons](https://github.com/FisherXZ/Sourcecado/issues/191) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#192: P2: Dark mode has no user control and native chrome stays light](https://github.com/FisherXZ/Sourcecado/issues/192) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |
| [#193: P2: Settings top is unscannable, diagnostics offer no next step, bundle wants a memorized run ID](https://github.com/FisherXZ/Sourcecado/issues/193) | Deferred to keep the active teammate backlog at eight focused issues. The description, acceptance criteria and discussion remain available for reopening. No implementation or fix is claimed. |

## Parts kept for later

- #181 now covers shared status wording and existing Connect/Fix actions. Live
  probes, last-verified timestamps and icons are deferred.
- #165 includes the send/enrich service extraction from #167. Other router,
  client and store refactoring remains deferred.
- Closing #134 does not certify a release. Its acceptance checklist and prior
  human decisions still apply whenever that work resumes.

This cleanup changes priorities and issue wording. It does not claim to fix the
product behaviors described in closed issues.

## Product scope

Shared login, team tenancy, hosted deployment, auto-send and background bulk
enrichment remain outside the current scope. LinkedIn/Apify v2 and larger
benchmark/release-gating work remain deferred. These decisions were previously
listed in the root TODO file.
