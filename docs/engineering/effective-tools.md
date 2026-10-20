# Effective model tool catalog

Status: active-stack engineering reference.

The schemas sent to a model for a Sourcecado run are the sole capability surface for that run. The catalog is rebuilt immediately before scheduled and interactive turns from:

1. the registered Sourcecado and connected MCP schemas;
2. the runtime-owned sourcing or buddy narrowing policy;
3. any validated persona declaration, which may narrow but never broaden that policy;
4. current connector availability;
5. active Sourcecado workspace grants, including read/write and shell authority; and
6. the schema-level approval class derived from the runtime permission policy.

Persona prose and frontmatter do not grant tools. The built-in personas declare no tools. A custom declaration is treated only as an additional allowlist restriction, and an unknown or misspelled name fails composition clearly.

Filesystem tools require an active workspace grant. Write tools additionally require a read-write grant. Shell tools require a read-write grant with shell authority. `request_directory` remains available so the operator can deliberately create the contract; it grants no filesystem or shell authority by itself.

Each effective schema keeps its registered shape and receives a content-free approval annotation: `auto`, `approval_required`, or `conditional`. Provider calls outside the supplied catalog are persisted as failed tool results and never execute.

Prompt diagnostics expose only ordered effective tool names and approval classes. They never include descriptions, parameter schemas, arguments, connector output, credentials, or prompt prose.

## Local CRM chat tools

`crm_tools.py` registers eight sourcing tools. Task reads, creation, updates
(including completion, reopening and cancellation), task-edit undo, and
description-slot saves use `TaskService` and `PersonStore.patch_description`,
the same writers used by HTTP. Successful write results include
`board_changed: true`, which refreshes the existing Contacts and person views.
No connector or credit-spending interface enters the CRM adapter.

The local tools are automatic under the sourcing policy and remain available
when Gmail, Apollo or the model's other connectors are disconnected. Buddy
does not receive them. Read tools may retry automatically; writes remain behind
the existing run fence and require an operation id and bounded reason. Task
edits and undo require the current task version; descriptions require the slot
version, available through `board_get` (initially zero). The operator's saved
timezone controls task calendar dates, just as it does for the app.

Relative reminder dates use the trusted director request timestamp in that
IANA timezone, rather than a later tool-call clock. The supported forms are
today, tomorrow, a weekday (today when it is that weekday, otherwise its next
occurrence), and an ISO date. The supplied date must match this result; an
omitted date cannot silently drop an explicit deadline. Conflicting dates,
“next Friday”, and other ambiguous forms ask for clarification. Possessives
such as “on Monday's call” describe context and remain undated.
Deadline suffixes are checked as complete clauses, including their modifiers;
every unresolved `by` or `due` clause asks for clarification, including unknown,
ordinal, numeric, clock-time and vague deadlines. Only the exact delivery
methods email, e-mail, phone, text, mail, post and hand exempt a `by` clause.
Ordinary topical `on` clauses and possessive weekdays remain context;
calendar-, numeric- or time-looking `on` clauses must resolve or clarify.
Unsupported second deadlines cannot be silently ignored. An exact saved
operation can replay after restart or a day boundary without moving its date;
the write service still checks its complete fingerprint and source access.

The actor saved by chat is always `assistant`. A separate human-protection
decision comes from the current authenticated director-channel `Directive`,
never a model-supplied actor, authority flag, source reference or reason.
Recognizable explicit commands such as “Remind me …”, “Mark that task done”,
“Change the description to …” and “Undo …” protect the human's requested fields.
Optional `request_quote` must occur in that current message; edited text must
also occur in it. Every chat mutation requires a verified command, including
when the model omits the quote. This deliberately narrow English matcher asks
for explicit wording when a request cannot be verified and saves nothing. It
does not establish general natural-language intent understanding; negated and
conditional wording also requires clarification. Scheduled
work cannot mint a director chat directive. Background inference belongs to
the separately gated analysis services (#209–#210); the task service rejects
assistant lifecycle changes without a director request until task-specific
fulfillment evidence is implemented. Its inferred text writes remain
assistant-owned and cannot replace human-protected fields.

Each chat turn permits one successful CRM operation, including a successful
no-op. The runtime binds that authority to the tool, operation id, canonical
request and resolved person/target. An exact replay may return its receipt;
another operation id, tool, target or changed request needs a new director
command. Failed validation can be corrected before the first success. Reads
remain available throughout. Each queued message gets a fresh turn budget;
scheduled prompts cannot create a director chat directive.

Authority is field-scoped. Completing, reopening or cancelling permits only
`state`; the model cannot add a title, details or date edit to that operation.
Task text/date edits accept one field per command in the form “Change the task
title/details/due date to: VALUE”, with the complete replacement value matching
the command. Due-date edits require an explicit ISO date; “Clear the task
details/due date” clears only that field. Unsupported compound requests ask for
separate explicit commands. When several tasks exist, include the exact task id:
“Change task task_<id> details to: VALUE” or “Clear task task_<id> due date”.
The named id must match the selected task; replacement text cannot select it.
New dated reminders require a date separate from
the task title; an incidental weekday in the title does not create a deadline.
Calendar words outside supported clauses, relative periods and clock times
require clarification instead of silently saving undated work.
Replacement comparisons ignore one final sentence punctuation character while
preserving full-value equality and the exact stored text. Literal punctuation
and empty text remain distinct.

An unspecified “description” means the general slot. The detailed slot requires
“detailed description”, and explicit slot wording must match the tool's slot.
Text replacement uses the complete value after “to:”; clearing requires
“Clear … description”. Releasing protection is a separate “Release …
description” command that preserves the text and cannot carry replacement text
or new sources. These limited forms fail with a clarification message instead
of expanding the director's authority to unrelated fields.

A person-bound chat cannot mutate another person's record. Outside a bound
chat, names resolve only on exact first/full-name matches; multiple matches
return candidates and a request for clarification. A bare id is accepted only
when the director supplied it.

Task and undo targets also require an explicit id, an exact task title after
“task”, or a unique eligible saved record. A person binding alone does not pick
between multiple tasks or changes. A named missing target cannot fall back to
another record. Private or incomplete work prevents guessing a generic target;
clarification responses do not reveal private titles.
Shared titles must be unique across eligible work, including source-restricted
records. A hidden duplicate produces a content-free clarification; an explicit
permitted task or change id can still select its public record. Exact operation
replay uses its existing receipt before checking post-write eligibility, then passes
through the writer's version/fingerprint/source checks.
Undo authority must first identify a task, or an exact task/change id. Generic
“Undo” and requests about descriptions, person status, attention, suggestions or
external actions cannot select the only eligible task. The current entity/id
checks also run before receipt replay.

Source references must be existing attachments on that person. Runtime source
grants govern private access; the model cannot
grant itself access. Sourced tasks and description reads are hidden when any
source is inaccessible, and partial reads are labelled. Invalid arguments and
stale writes produce structured failures without a success notification.

Task changes and operation receipts commit together in `people.db`. Source
access is checked inside that write transaction, so a concurrent person
revert cannot remove an attachment between validation and the new task receipt.
Existing pre-upgrade task fingerprints remain replayable. Description saves can also
carry an operation id: their durable receipt is stored atomically in the
existing person timeline under `crm-operation:<id>`, with a request fingerprint
and saved response. Same-id retries return that response; different arguments
conflict. Operation ids cannot be reused across task and description writes.
The run ledger records only generic change pointers and permitted source ids;
it does not copy task text, description text, source bodies, or request reasons.
These pointers merge into the existing effect-completion transaction. Each
fenced call has one pending checkpoint and one completion checkpoint, including
replay; pointer writes do not append a second completion. If effect settlement
rolls back, the pointers and checkpoint roll back with it. External-effect
quarantine and approval fencing retain their existing behavior.

This is a partial integration for #206 while prerequisites are pending:

- `crm_list_attention` and `crm_set_attention` report
  `crm_capability_unavailable`, naming #204. They cannot yet match Contacts
  attention ordering/freshness or save manual flags.
- `crm_resolve_proposal` reports the same unavailable code, naming #205.
- `crm_undo_change` supports existing task edits only. Task creation is cancelled
  through `crm_update_task`; description/attention/proposal undo awaits #205.
- Description receipts use person timeline ids, not `crm_changes`. A shared
  description/proposal change service and field-scoped undo remain integration
  work for #205; broad person revert is not used as a substitute.
- Evidence analysis, source-derived proposals and refresh freshness are separate
  from chat tools (#208–#210). This adapter does not claim those capabilities.
