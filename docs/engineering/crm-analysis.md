# CRM evidence analysis

Tickets #209 and #210 add a bounded backend worker and authenticated app trigger. It
writes a general description of at most 500 characters and a detailed one of
at most 8,000 characters. These slots remain separate from reviewed handoff.
The same result extracts sourced email tasks. This is a draft delivery slice: #205's review UI and #208's background Gmail
collection/trigger are not present. No recurring analysis or complete CRM
release is claimed.

`crm_analysis.AnalysisService` receives the current app provider, its configured
failovers, a narrowed `ConversationReader`, and caller-owned source grants.
`POST /v1/people/{id}/analysis` invokes it; `{ "retry": true }` explicitly resets
an exhausted attempt limit. `GET` on the same path reports durable analysis
freshness separately from mail freshness. There is no Agent Run. List rendering
never calls a model.

## Evidence and model boundary

The worker consumes Gmail events already filed on a person. Full inbound and
outbound message reads use the tracked thread and exact sender/recipient
index from reply filing. It keeps the shared-address, forwarded-thread and
multi-person refusals. This worker does not discover new Gmail messages;
#208 must collect those events and call the pending analysis query.

The reader exposes metadata and full-body reads only. It cannot send, draft,
enrich or search the mailbox. It does not widen `InboundReader`. Restricted
source attachments require the caller's existing attachment-ID grant before a
body read. Sources created from older Gmail event pointers contain IDs and
metadata, never copied mail bodies.

Each pass is capped at 24 messages and 32,000 body characters. A body at the
legacy Gmail reader's 8,000-character cutoff is partial because that reader
cannot prove completeness. Missing bodies, header conflicts, quoted-only
messages, greetings, ambiguous ownership and overflow preserve existing text.
The immutable model manifest maps opaque mail keys to validated person-owned
source IDs, metadata/body fingerprints and direction. Current source ownership,
identity and grants are rechecked after every body read and directly before
model dispatch, including earlier messages and aliases for the same mail.
A revoked or changed source produces partial state without entering the prompt.
Full bodies exist only in memory. They are sealed in the existing untrusted evidence envelope. The
model receives no tools or target-person argument; output cannot select a
person, mint a source or create another effect. Output is bounded and must
provide both description slots, a bounded reason, source keys, sufficient
context, and an explicit `tasks` array (empty when there are no candidates).
Missing task output fails the whole stage rather than marking extraction done.
At most 12 candidates cite exact original request text and an opaque mail key.
The model cannot supply task dates, access grants, or source identities. A model
declaration alone cannot bypass the structural source gates.

## Transactions and protection

`PersonStore.patch_description` and analysis use the same no-commit description
primitive. The public UI/chat writer owns its transaction; analysis owns one
transaction covering both slots, tasks, CRM history, proposals, request evidence
and its checkpoint. `TaskService` exposes caller-owned no-commit create/update
primitives; HTTP and chat wrap those same writers in their own transactions.
It rechecks the person, source permissions and evidence snapshot after model
work, then checks the local guard and claim inside the commit transaction.
One stale result can retry against current inputs; a second remains pending.

Assistant-owned slots update automatically. Human-owned slots, including an
intentional blank, produce `crm_proposals` rows. Automatic interpretations also
have accepted proposals linked to their change IDs. A proposal's identity uses
the target intent and stable supporting message/source/body identities;
ordinal prompt keys and unrelated new mail cannot revive a dismissal.

The backend exposes person-scoped proposal list and accept/dismiss endpoints
for integration with #205. Acceptance checks proposal and target versions and
keeps human protection. A stale acceptance leaves the proposal pending. This
slice does not implement the review UI, edited acceptance, or chat proposal
resolution. Accept/dismiss saves a canonical request fingerprint and original
response in the proposal's nullable resolution receipt, in the same transaction
as the target mutation and resolution. Exact retries check current source access
and return that receipt before checking the later proposal version. Changed
arguments conflict. Undo preserves the receipt, so an old acceptance retry
returns its original response without accepting the dismissed proposal again.
Task create/update proposals use the same accept/dismiss receipt protocol and
TaskService writers. Acceptance gives the task human authority; date reconciliation
retains its existing details and state. Stale target versions remain pending.
Multiple possible targets require a later review choice rather than creating work.

## Email tasks and completion

Automatic creation requires an exactly attributed inbound full message, sufficient
original unquoted content, a direct actionable request, and a grounded title.
The service reads current tasks and protection after generation; task changes also
invalidate the generation guard. A possible match, including a model-cited permitted
task ID, produces reconciliation review. Matching retains the supported action
and object and reads the saved source-request provenance. Reviewing, preparing,
and sending the same object remain different obligations; explicit original and
revised deliverables remain separate. A stripped word subset or generated title
cannot merge them. An uncertain human paraphrase stays pending for target review;
acceptance cannot move another obligation's date. Legacy date proposals without
request proof also need review. Missing, foreign and private task hints
cannot authorize a new task. Separate request sentences and explicit action clauses
keep separate evidence spans, including two promises in one sentence.

Weekday and ISO deadlines require one final explicit `by DATE`, `due DATE`, or
`due on DATE` clause. Plain `on DATE` remains unsupported because it can describe
an object's discussion or meeting. Quoted object titles do not authorize dates;
today/tomorrow also work as final temporal adverbs. Relative dates use Gmail's validated `internalDate` and
the saved operator IANA timezone. Header date fallback, refresh time and model
timestamps cannot establish a relative deadline. Bare same-day weekdays, next
Friday, bare/topical/past weekdays, conditional/negated/conflicting text, unknown date clauses, unsupported
relations such as after Friday, and incidental weekdays remain undated proposals.
Possessive weekdays do not become deadlines. This is a conservative English
grammar; it does not claim broad natural-language date support.
Correction, retraction and already-fulfilled wording anywhere in the original
source blocks automatic creation and date inference, including text outside the
model's cited request. Such interpretations stay undated review proposals.

Task suppression uses stable person-owned source/message/body identity plus a
canonical original request span. Model titles, citation punctuation, expanded
citations, ordinal mail keys, unrelated mail and prompt/stage versions do not enter
that identity. Accepted automatic interpretations remain linked to their changes;
undo cancels the unchanged inferred task as a compensating version and dismisses
its interpretation. History remains intact. Restart and unrelated new mail cannot
recreate unchanged dismissed, undone or completed work. Human/chat-created tasks
keep their existing Cancel behavior.

Every completion candidate remains a sourced review proposal. Current approved-send
receipts have no validated task-to-draft binding; a thank-you, an outgoing message,
an unknown send outcome, or model confidence cannot prove a particular task was
fulfilled. This slice never completes work automatically or changes person status.

Task projections expose `source_links: [{id, label, url}]`; history adds the same
links and a current `can_undo` boolean. Links use currently permitted person-owned
pointers with fixed labels. Gmail URLs require a safe thread ID matching the
person's filed message and tracked thread. Arbitrary source URLs/titles are not
rendered as links. HTTP and chat hide inaccessible sourced work. Automatic creation
Undo uses the same version/eligibility checks through HTTP and trusted chat; chat's
explicit-command, private-target and one-mutation rules remain in force.

Description history is available at
`GET /v1/people/{id}/descriptions/{slot}/changes`. The shared CRM Undo endpoint
routes a description change to field-scoped compensating history. It refuses a
changed slot, restores text/authority/source metadata together, and suppresses
the undone inference's support. Unrelated slots and tasks remain intact.
Historical description event-operation receipts continue to replay. Person
snapshots retain description authority and sources; person revert does not
revert independently versioned tasks.

## Retry, restart and accounting

`crm_analysis_state` stores latest/analyzed revisions, evidence fingerprint,
attempt count, next retry time and a fenced, expiring claim. Pending work is
`latest_revision != analyzed_revision`; `pending()` applies the saved retry and
claim limits. A first invocation establishes pending state before a cancellable
connector read. Connector failures persist a safe failure code and pending
revision. Interrupted provider claims consume an attempt and expire after
180 seconds; cancellation records its failure and releases the claim.

Each revision has at most three persisted attempts with bounded exponential
backoff. A stale retry consumes the same limit. Only explicit Retry resets it.
The collector's unchanged public mail-pointer creation is bookkeeping, so a
first body-read failure cannot invent a revision or reset the attempt count.
Real source/privacy/grant changes and changes to the person's tracked-thread
attribution remain significant. Saved backoff, active claims and exhausted
attempt limits are checked before connector reads.
A request uses one configured provider; later saved attempts use configured
failovers, without nested provider retries. The existing budget meter bounds
model work and records usage/cost for every request. Terminal usage supersedes
earlier streamed usage. Unknown cost and usage remain null and carry the
meter's unpriced/unmeasured counts. A source check that blocks dispatch records
no model request, no provider/model identity and no invented cost; its analysis
attempt can still have a durable partial outcome.

`crm_analysis_attempts` records input revision, prompt version, evidence
manifest, provider/model, usage, estimated cost, budget result, safe failure
code, outcome and change/proposal IDs. It never stores bodies, prompts,
provider error text, tokens, or hidden reasoning. Successful results and the
analyzed revision commit together, so restarting after commit finds no work
to replay. Failures preserve existing descriptions.

## Upgrade and verification

`people.db` v7 is registered in `migrations.py`. A new empty, unversioned
database records v7 after initialization, before current-version writes.
An existing database keeps its recorded version, including unversioned legacy
state and unsupported future versions; constructor table probes never adopt it.
Stop the backend and use `make doctor-repair` before restarting a legacy/v5/v6
installation. This path backs up and migrates the store; constructors alone
cannot widen SQLite's old CRM-history constraint. The worker checks both the
declared version, history and task-stage shape and returns `crm_migration_required`
before model spend. Historical DDL remains frozen. The registered v6→v7 step adds
the stage marker and `crm_task_evidence` span/FK records without AI backfill.
Already-analyzed v6 inputs become eligible for the new stage. Person snapshot
revert preserves current source rows supporting independent tasks, history and
proposals, including current restrictions; it does not revert task work.

Until migration, safe human description saves and operation replay retain the
pre-v6 event-receipt behavior. They do not add v6 CRM-history rows that an old
migration cannot represent. Inferred writes and scoped undo require the current
schema. Replaying an old receipt after migration still returns the saved result.

Offline tests use read-only connector and model fixtures. They cover both
slots, sources, protected blanks, stale generation/acceptance, private and
mismatched mail, outbound attribution, injection/tool output, incomplete mail,
retries, failover and unknown accounting, cancellation, atomic rollback,
dismissal after prompt-key changes, field-scoped undo, real app routes,
restart, populated v5/v6 and legacy migrations, migration rollback, foreign keys and snapshot
restore. No live Gmail/provider call, proposal review UI or native background
refresh demonstration is claimed. Task fixtures cover explicit/relative/vague dates,
conditions, quoted and partial mail, wrong/shared/private attribution, corrected
human dates, same-message promises, citation boundary variation, stale acceptance,
runtime chat Undo, retries/restart, and rollback after task/proposal/checkpoint writes.
Synthetic fixtures prove these boundaries, not live extraction precision or recall.

A headless Chrome check used the actual frontend and an isolated FastAPI backend
with synthetic read-only mail and model fixtures. Analysis created one sourced
task visible in Contacts and the person file. Person-file Undo cancelled that
same task; refreshing unchanged evidence and reloading the global list preserved
the cancellation. Source links and change reasons rendered without page errors.
This check does not exercise the native shell or live connectors.
