# Contacts attention implementation

Source: September 28 Contacts attention and shared tasks specification supplied by the user.
Current guardrail: global Tasks includes all live saved people; Contacts remains the three sequence states.

## Approach

Reuse TaskService, PersonStore, tracked Gmail collection, description editors and the approved-send flow.
UI and chat call the same validation services. SQLite transactions commit records, audit and retry receipts together.
Email collection and model analysis have independent durable progress. Analysis has no connector write capability.
Human authority, evidence ownership and entity versions are checked in code, not entrusted to prompts.

## Ordered checkpoints

1. Baseline and shared tasks: focused existing tests; include pre-sequence people in global Tasks without changing sequence membership.
2. Attention: manual flags, deterministic ordering, nearest task, review reasons and local authenticated reads.
3. Preview: keyboard-accessible person preview, editable descriptions/tasks, source activity, unsaved-edit protection and narrow-window return.
4. Shared assistant operations: typed CRM tools through the same services and permission catalog.
5. Evidence maintenance: proposals, scoped audit/undo, protected descriptions and tasks, evidence revision checks and duplicate suppression.
6. Background analysis: read-only model adapter, bounded refresh, coalescing, persistent attempts/failures and restart recovery.
7. Verification: focused and regression suites, frontend build, browser demonstration, migration/rollback and documented native limits.

Each checkpoint gets behavior tests before implementation. Follow actual repository commands and use isolated external state.
Do not modify the archived hosted implementation or introduce auto-send, auto-enrichment or a new scheduler framework.

## Risks

- Windows sandbox temporary directories initially prevent test setup: relocate temporary files into the permitted external visualization directory.
- Native packaging targets macOS; report Windows verification limits honestly.
- Model interpretation needs real, consented examples before claiming production precision; offline fixtures establish boundary behavior only.
- Snapshot/revert must carry description authority and manual flags; task history stays independent.

## Teaching approach

Explain each checkpoint in commentary: behavior, tool, reason, test result. Use small Mermaid diagrams for data flow.
Keep operational details out of the product UI.
