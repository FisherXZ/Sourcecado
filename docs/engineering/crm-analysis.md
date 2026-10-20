# CRM description analysis

Ticket #209 adds a bounded backend worker and authenticated app trigger. It
writes a general description of at most 500 characters and a detailed one of
at most 8,000 characters. These slots remain separate from reviewed handoff.
This is a draft delivery slice: #205's review UI and #208's background Gmail
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
source IDs, metadata/body fingerprints and direction. Attribution is pinned to
the index before connector I/O. The worker rechecks each message's current
thread, relevant addresses and sending accounts before and after every body
read and directly before model dispatch, including all earlier messages.
Inbound and outbound mail share the same exact-match classifier. These narrow
checks exclude the worker's public source-pointer bookkeeping. Current source
ownership, identity and grants are also rechecked at the body and dispatch
boundaries, including earlier messages and aliases for the same mail. Changed
attribution or a revoked source produces partial state before model dispatch.
Full bodies exist only in memory. They are sealed in the existing untrusted evidence envelope. The
model receives no tools or target-person argument; output cannot select a
person, mint a source or create another effect. Output is bounded and must
provide both description slots, a bounded reason, source keys, and sufficient
context. A model declaration alone cannot bypass the structural source gates.

## Transactions and protection

`PersonStore.patch_description` and analysis use the same no-commit description
primitive. The public UI/chat writer owns its transaction; analysis owns one
transaction covering both slots, CRM history, proposals and its checkpoint.
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
returns its original response without accepting the dismissed proposal again. Task proposal kinds are reserved in the durable schema for #210;
this worker resolves description proposals only.

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

`people.db` v6 is registered in `migrations.py`. A new empty, unversioned
database records v6 after initialization, before current-version writes.
An existing database keeps its recorded version, including unversioned legacy
state and unsupported future versions; constructor table probes never adopt it.
Stop the backend and use `make doctor-repair` before restarting a legacy/v5
installation. This path backs up and migrates the store; constructors alone
cannot widen SQLite's old CRM-history constraint. The worker checks both the
declared version and history shape and returns `crm_migration_required` before
model spend. Historical v4→v5 DDL remains frozen.

Until migration, safe human description saves and operation replay retain the
pre-v6 event-receipt behavior. They do not add v6 CRM-history rows that an old
migration cannot represent. Inferred writes and scoped undo require the current
schema. Replaying an old receipt after migration still returns the saved result.

Offline tests use read-only connector and model fixtures. They cover both
slots, sources, protected blanks, stale generation/acceptance, private and
mismatched mail, outbound attribution, injection/tool output, incomplete mail,
retries, failover and unknown accounting, cancellation, atomic rollback,
dismissal after prompt-key changes, field-scoped undo, real app routes,
restart, populated v5 migration, migration rollback, foreign keys and snapshot
restore. No live Gmail/provider call, browser review UI or native background
refresh demonstration is claimed.
