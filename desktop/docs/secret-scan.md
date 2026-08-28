# Secret absence scan

Confirms a registered secret's value is absent from Sourcecado's local
conversations, transcripts, events, and logs. It never prints the value it is
looking for and never prints a value it finds.

This is one piece of the response to a revoked credential (GitHub issue #38):
after a credential has been rotated in the vault, this answers "does the old
value still show up anywhere else on this machine?" without ever putting that
value in a shell, a history file, or a ticket.

## Running it

```
make secret-scan                # scan for every value currently registered
make secret-scan KEY=apollo     # scan the live vault value for one key
make secret-scan KEY=apollo REVOKED_FROM=./pre-rotation-secrets.json
```

Or directly:

```
cd desktop
.venv/bin/python -m coworker.secret_scan
.venv/bin/python -m coworker.secret_scan --secret-key apollo
.venv/bin/python -m coworker.secret_scan --secret-key apollo --revoked-from ./pre-rotation-secrets.json
.venv/bin/python -m coworker.secret_scan --secret-key apollo --json
.venv/bin/python -m coworker.secret_scan --state /path/to/state
```

The key is the name a credential is registered under in the secret store
(`apollo`, `google`, and so on) -- not the credential itself. Typing the key
is safe.

After rotation the live vault holds the replacement. `KEY=apollo` alone would
search that replacement and can report clean while conversations and events
still hold the revoked value. Copy `secrets.json` to a mode-0600 snapshot
*before* writing the replacement, then pass that snapshot as `REVOKED_FROM`
(or `--revoked-from`). Needles are read from the snapshot and never fall back
to the live vault. The snapshot path is a filename, not the value; the value
never leaves this process as text.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Clean. The value was searched for and not found. |
| 1 | Found. The value is present in at least one store. |
| 2 | Nothing to search for -- an unknown key, an empty vault, or a scan that failed before it could run. |

Code 2 is deliberate, not an afterthought: a scan that found nothing to search
for and reported "clean" anyway would be worse than no scan at all.

## What it scans

Every store in `coworker/migrations.py`'s registry that is present on disk,
except the three marked `secret_bearing` -- `secrets` (`secrets.json`),
`mcp_config` (`mcp.json`), and `dotenv` (`.env`). Those are the vault: they
exist to hold the *current* credential, `secrets.json`'s own registry entry
says it must never be read into a report, and scanning them for an old value
answers a different question (did rotation take) than the one this tool
answers (did the value leak somewhere else).

A store added to the registry later is picked up automatically, by kind, the
next time this runs:

| Kind | How it is read |
| --- | --- |
| SQLite | Every table, every column, every row, through a read-only handle. |
| JSONL directory / log | Every line of every file. |
| JSON document | The file's raw text. |
| Directory | The raw text of every file inside it. |

**Every Doctor backup is scanned too**, not just the live stores.
`<state>/backups/<id>/` holds a copy of whatever a repair touched, taken
*before* the change, so a backup made before a credential was rotated can
still hold the old value even once every live store reads clean. The scan
reads `<state>/backups/`'s manifests the same way `doctor backups` does and
scans whatever each one actually copied. A backup match is reported under its
own id, `backup:<backup_id>:<store_id>`, never folded into the live store's
finding -- "the live store is clean but a backup is not" needs a different
fix (delete or replace the backup) than "the live store still has it" does.
The vault exclusion applies here too, checked independently of what a backup's
manifest claims: Doctor never copies a `secret_bearing` store into a backup in
the first place, and this scan skips one by its own registry lookup even if a
manifest said otherwise.

**Not scanned:** the vault stores above (live or inside a backup), and
`agent_runs.db`, which is not yet in `migrations.REGISTRY` -- it will be
picked up automatically, with no code change here, once it is registered.

## Bounded and never the value

A finding names a store id, a record or file identity -- a table and rowid, a
transcript file and line number, a JSON document's path -- and a count. It
never names the text that matched, never a window of characters around it,
and never the search term itself. The value is never assembled into any
finding, so there is no redaction filter standing between a mistake and a
leak: the object that gets printed or serialized to JSON never held the value
to begin with.

Each store's detail list is capped at 8 identities. A store with ten thousand
matches reports a count of ten thousand and seven example locations plus an
`and N more` line, not ten thousand lines.

`desktop/tests/test_secret_scan.py` plants a distinctive value, proves the
scan reports a match for the store carrying it, and only then asserts the
value is absent from the rendered report, the JSON report, and the CLI's
stdout and stderr.

## What this does not do

It does not touch Google Drive, does not inspect revision history, and does
not decide whether a rotation succeeded. It reads one local JSON file -- the
pre-rotation snapshot when `REVOKED_FROM` is set, otherwise the live vault --
to learn what to search for and reads local state to search. Closing out
issue #38 still needs a human to review Drive's revision-retention controls,
purge or replace the exposed revision, and record the accepted residual risk
-- this tool only answers the one question stated above.
