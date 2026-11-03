# CRM integration verification — November 2, 2026

Status: implementation and verification in progress. This record does not certify release acceptance.

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

Final integrated results will be recorded after the combined suite, build, evaluations and local browser pass complete. Per-feature branch results are supporting evidence, not a substitute for the integrated checks.

## Acceptance limits

All connector, provider and mail examples in implementation verification use fake services and isolated synthetic state. No real mail was sent, no Apollo credits were spent and operator state was not migrated. Real Gmail/account renewal, permitted interpretation examples, signed/native distribution and Fisher's #212 desktop acceptance remain separate checks. #165 and #240/#241/#243 are not completed by this integration.
