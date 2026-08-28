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
make secret-scan KEY=apollo     # scan for one registered value by key
```

Or directly:

```
cd desktop
.venv/bin/python -m coworker.secret_scan
.venv/bin/python -m coworker.secret_scan --secret-key apollo
.venv/bin/python -m coworker.secret_scan --secret-key apollo --json
.venv/bin/python -m coworker.secret_scan --state /path/to/state
```

The key is the name a credential is registered under in the secret store
(`apollo`, `google`, and so on) -- not the credential itself. Typing the key
is safe; the value it points at is read from `secrets.json` and never leaves
this process as text.

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
not decide whether a rotation succeeded. It reads one local JSON file to learn
what to search for and reads local state to search. Closing out issue #38
still needs a human to review Drive's revision-retention controls, purge or
replace the exposed revision, and record the accepted residual risk -- this
tool only answers the one question stated above.
