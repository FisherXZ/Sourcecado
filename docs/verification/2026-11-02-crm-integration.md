# CRM integration verification — November 2, 2026

Status: first integration milestone verified locally. This record does not certify release acceptance.

## Scope

Integrate the reviewed open CRM work on current main without restoring the archived hosted app. The integration retains the local Person File, three active sequence states, explicit reviewed sending and deliberate enrichment.

Features: saved-person search and Apollo identity recognition (#293), task-based reviewed follow-up (#248), live connector health (#256), Gmail activity (#244), source-backed descriptions (#245), conservative email task extraction (#246), guarded chat (#239), and Contacts attention, preview, suggestions and scoped Undo (#247).

## Review-driven changes

- Keyboard activation opens the result actually focused in the saved-person finder.
- Sending verifies the actual Gmail conversation for new drafts as well as replies.
- Connector checks retain MCP failure state and bind cached outcomes to the credential actually checked. Late renewal cannot restore a disconnected or replaced grant.
- One registered, backed-up schema upgrade recognizes both prior CRM branch layouts. Legacy tasks, descriptions, proposals, source permissions, history and operation receipts remain intelligible.
- One analysis worker supplies descriptions and tasks. The Contacts facade and guarded chat use the same saved operations; model-supplied reasons do not grant human authority.
- Mail attribution is checked again after connector I/O, before person-bound data is committed.
- Status, attention and accepted-suggestion Undo preserve unrelated work and refuse superseded changes.
- Contacts view choices and supported sorts persist, with usable defaults and Reset view.

## Verification results

- Full Python 3.14 backend: **2,820 passed, 3 skipped**. One existing Starlette/httpx deprecation warning.
- Full Node 24.21.0 frontend: **1,383 passed**, 99 files. TypeScript and the Vite production build passed. The existing bundle-size warning remains.
- Deterministic sourcing scenes: **15/15 passed**.
- Local browser, real React/FastAPI with isolated synthetic state: remembered sorting after reload; flag/status save and scoped Undo; suggestion acceptance and saved-task Undo; unsaved-preview protection on follow-up navigation; kept-person search; focused-person keyboard activation; 390px page containment; no browser page errors.
- Upgrade fixtures cover original v5, both v6 layouts and both v7 layouts, preserved human blanks, tasks/proposals, source restrictions, operation receipts and registered backup/rollback. Legacy unchanged dismissed/undone analysis stays suppressed; changed or unauthorized evidence cannot inherit that suppression.
- Independent failure reproductions cover wrong-thread send, MCP error normalization, credential replacement during renewal, changed ownership during mail reads, source revocation during draft generation and cached receipt replay, scoped Undo and post-prepare collection timing.

The final backend run used an isolated Python 3.14 environment installed from the hash-locked dependencies, including the pypdf 6.21.4 security patch and final legacy-description-retry compatibility fix. Backend and frontend dependency audits reported no known vulnerabilities. Per-feature branch test counts overlap with these totals and are not additive.

### Browser evidence

- [Desktop Contacts](evidence/2026-11-02-crm/contacts-1440.png)
- [390px Contacts](evidence/2026-11-02-crm/contacts-390.png)
- [390px person preview](evidence/2026-11-02-crm/preview-390.png)
- [Observed browser checks](evidence/2026-11-02-crm/browser-checks.txt)

All screenshots use fictional people and isolated fake services. They are implementation evidence, not a new reference-design decision.

## Acceptance limits

All connector, provider and mail examples in implementation verification use fake services and isolated synthetic state. No real mail was sent, no Apollo credits were spent and operator state was not migrated. Real Gmail/account renewal, permitted interpretation examples, signed/native distribution and Fisher's #212 desktop acceptance remain separate checks. #165 and #240/#241/#243 are not completed by this integration.
