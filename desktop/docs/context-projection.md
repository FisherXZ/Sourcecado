# Bounded context projection

Status: active-stack engineering reference for issue #58.

Fisher approved `context-projection-v1` on 2026-08-27. The packet is
`docs/superpowers/plans/2026-08-27-context-projection-coauthoring.md`. This page
describes what the activation actually does.

## What changed in the prompt

The `saved_memory` section used to be a bounded full-store dump. Every row from
`list_memories()` was joined oldest first and the joined string clipped at 4,000
characters. That favoured the oldest rows, could cut a row in half, and could
not tell a preference of the director's from a fact about one person.

It is now a projection. `ConversationStore.memory_projection_items()` emits
already-scoped `ProjectionItem` values for the classified operator preferences,
and `server._saved_memory_body` selects from them through
`prepare_context_projection` under `DEFAULT_PROJECTION_POLICY`.

Two caps apply and the first one reached wins:

- the 256-token `operator_preference` category budget, 64 tokens per item, with
  no borrowing from any other category;
- the existing 4,000-character envelope for this section, which together with
  the person file's 2,000 is the approved 6,000-character combined envelope.

Selection is whole items under both caps, so a preference is never cut in half
and its Source Reference is never cut off. Tokens are counted with
`store.projection_tokens`: `ceil(len(utf-8 bytes) / 3)`. That is an explicit
budget unit, not a claim about any provider's billed tokens.

## What a memory row carries

`club.db` schema version 2 adds nine columns to `memories`: `category`,
`classification_status`, `person_id`, `session_id`, `source_ref`, `updated_at`,
`fresh_until`, `sensitivity`, and `claim_key`.

Only one predicate lets a row into the prompt:

```sql
classification_status = 'classified' AND category = 'operator_preference'
AND person_id IS NULL AND session_id IS NULL AND sensitivity = 'standard'
```

It is a positive allowlist, so any state the code does not recognise is withheld
rather than leaked.

Evidence state is derived at projection time, not stored:

- `conflicting` — two or more classified rows share one `claim_key`. Both are
  projected, and each carries both Source References, so the projection never
  silently picks a winner.
- `stale` — the row's `fresh_until` has passed. Preferences have none by
  default; they go stale only when the director changes one.
- `current` — everything else.

## The migration

`SCHEMA_VERSION` in `coworker/store.py` is 2, and `coworker/migrations.py`
registers the 1 to 2 step for `conversation_db`. Doctor plans it, backs the
store up first, and rolls it back if it raises.

Every memory row already on disk becomes `category = 'legacy_unclassified'` with
`classification_status = 'needs_review'`. Ids, content, `created_at`, the
per-row Markdown files, and `MEMORY.md` are untouched.

The step never reads a row's text. A row worded like a global preference and a
row stating a fact about one person are indistinguishable as text, so both wait.
That temporarily withholds well-worded rows, and it is the point: it stops an
existing person fact from silently becoming a global preference.

The step runs its statements one at a time. `sqlite3.Connection.executescript`
issues a COMMIT before it runs, which would end the transaction `_apply_store`
opens and leave the rollback with nothing to undo.

The store's constructor adds the same columns to an existing database so the app
never opens a database it cannot query, but it adds them without defaults. The
registry step is what writes the classification, so the backup covers the real
change.

## New writes

`store.remember()` records `category = 'unclassified'`,
`classification_status = 'needs_review'`. Nothing infers a category from the
content. `store.memory_update()` sends a rewritten row back for review, because
rewritten text is text nobody has classified.

A row becomes a global preference only through `store.memory_classify()`, which
is reached only by `POST /v1/memory/{id}/classification` — a director action in
the UI. It refuses any row carrying a `person_id` or `session_id`: a fact about
one person belongs on that Person File through the existing Board contract.

The `remember` tool passes no scope, so every model-driven write is ambiguous
and waits. `coworker/tools.py` is where a director's explicit "remember this as
a standing preference" would have to be carried; that parameter does not exist
yet.

## The backlog is visible

Withholding is user-visible: rows the director saved stop appearing until they
are classified. It reads as Sourcecado having forgotten things, so the count is
deliberately in front of the operator.

- `GET /v1/memory/classification` returns `needs_review`, `classified`, and the
  waiting rows.
- The global rail carries a **Memory** destination with a badge showing the
  waiting count, refreshed on navigation and on window focus.
- `#/memory` lists each waiting row with two actions: keep it as a global
  preference, or delete it. The page says where a fact about one person belongs
  instead.

## Known bound to watch

The 1,024-token person-evidence cap is the number most likely to need
revisiting. At 160 tokens per item that is roughly six evidence items for a
director working one person deeply. It ships as specified. The no-borrow design
means raising it later is a contained change; do not raise it preemptively.
