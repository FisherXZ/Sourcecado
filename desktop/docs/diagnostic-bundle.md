# The diagnostic bundle

Status: active-stack engineering reference. Covers the export surface for one
failed or interrupted run, its three redaction layers, and how to verify an
archive offline.

A diagnostic bundle is a local ZIP file that joins run evidence, versions,
health history, state integrity, connector status, and structured log records
for one failed run. Its whole purpose is to be handed to someone else. That is
what makes it the most dangerous artifact Sourcecado can produce, and it is why
this document leads with what cannot get into one.

## Words used here

- **Bundle** — one ZIP file describing one failed run or one state finding.
- **Subject** — the exact run id, or the exact state-integrity check, an export
  started from. Every bundle names one.
- **Member** — one file inside the archive.
- **Scan** — the pre-export check that refuses to build a bundle at all.
- **Fail closed** — a matched scan produces nothing. Not a redacted bundle, not
  a warning file, not an empty folder.

## Running it

The operator surface is Settings → Diagnostic bundle. It has two steps and they
are separate on purpose.

1. **Review bundle.** Assembles the bundle, runs the scan, and shows a bounded
   preview: the subject, every evidence category with its description, what is
   left out, and the counts. Nothing is written.
2. **Save bundle to this Mac.** Repeats the work and writes one file to
   `<state>/diagnostics/sourcecado-diagnostic-<bundle id>.zip`, mode 0600, and
   reports the path, the size, and the SHA-256.

Sourcecado never uploads a bundle. `coworker/diagnostic_bundle.py` opens no
network client and imports nothing that could; `test_the_bundle_module_opens_no_network_client`
holds that line.

The same two steps are available directly:

```
POST /v1/diagnostics/bundle/preview   {"run_id": "run-…"}
POST /v1/diagnostics/bundle/export    {"run_id": "run-…"}
```

Either accepts `{"check": "...", "store_id": "..."}` instead, to start from a
state-integrity finding when there is no run to point at. An unknown run is a
404. Neither field is a 400.

## What a bundle holds

| Member | Holds |
| --- | --- |
| `subject.json` | The run or finding this bundle is about, every evidence category with its description, and the exclusion list. |
| `environment.json` | Sourcecado's version and slice, the Python runtime, and the OS family, release, and architecture. |
| `run.json` | The run's lifecycle codes, model attempts and durations, tool calls and durations, token counts, approval decisions, recovery events, and how it ended. |
| `health.json` | The last 50 runs by state, trigger, and outcome. |
| `state.json` | Every store's version against the registry, pending migrations, integrity findings, and runtime dependencies. |
| `connectors.json` | Each connector's identity, status, and missing permissions. |
| `logs.json` | Up to 100 structured event records from the run's session. |
| `checksums.txt` | SHA-256 for every evidence member, in `shasum -c` format. |
| `manifest.json` | The bundle version, the package identity, and SHA-256 plus size for every other member. |

## What a bundle never holds

- Prompts, persona text, and message bodies.
- Source excerpts, source titles, and source URLs.
- Tool arguments, tool results, and command output.
- Credentials, authorization headers, and OAuth grants.
- Private model reasoning.
- Raw home paths.
- The machine's hostname and the connected account's email address.

### Free text is carried as a length

This is the rule that does the most work, and it costs something, so it is
stated plainly.

A run records two bounded free-text fields, `reason` and `error_summary`. Both
are already truncated and passed through the write-time redactor. Both are
still written from caller text: a summary of a failure routinely quotes a file
path, a provider's error body, or the message the run was working on. A bundle
therefore carries `reason_length` and `error_summary_length` and never the text.
The same applies to an event log's `message`, `delta`, and `text`.

What replaces it is `error_class`, the tool name, the lifecycle code, the
duration, and the evidence value. That is enough to say *which tool failed, with
what class of error, after how long, with what left unknown*. It is not enough
to read the provider's own error string. That trade is deliberate: an error
string is the single most likely carrier of every category in the list above.

The one exception is `state.json`. Its findings are copied verbatim from the
state report, whose summaries and details are structurally generated from store
ids and counts rather than from caller text, and are separately bounded to 8
detail lines of 200 characters each. `docs/doctor.md` documents that contract.

## Evidence, not inference

Every section of `run.json` carries an `evidence` value, passed through
unchanged from the run receipt. The bundle does not define a vocabulary of its
own: `present`, `absent`, `partial`, `missing`, `ambiguous`, `unsupported`, and
`expired` are `coworker/run_evidence.py`'s words, and `docs/run-ledger.md`
defines them. Two vocabularies for one distinction is how they drift apart.

The distinction that matters most is `absent` against `missing`. "No source was
touched" and "we do not know whether one was" are different operator
situations, and a bundle that flattened them would be worse than no bundle.
`test_absent_and_missing_stay_different_through_the_projection` builds two
bundles — one from a run whose record has a hole, one from a run that ended
cleanly — and asserts the same empty section reads `missing` in the first and
`absent` in the second.

`test_the_bundle_reuses_the_merged_evidence_vocabulary` asserts every
`evidence` value anywhere in a bundle is a member of that enum, so neither side
can coin a word the other does not have.

`evidence_categories` in `subject.json` is a different thing despite the shared
word: it lists *which kinds of data* a bundle holds, not how well the record
supports a conclusion.

## Three layers, and why the third is independent

| Layer | Where | What it does |
| --- | --- | --- |
| 1 | `coworker/run_receipt.py` | A closed field allowlist over one run. Only identifiers, enums, counts, timestamps, and bounded runtime notes exist as fields. |
| 2 | `coworker/doctor.py` | Checks report what they counted, not what they read, and every summary passes through `redact()`. |
| 3 | `coworker/diagnostic_bundle.py` and `coworker/bundle_redaction.py` | A second projection that names its own fields, plus a path rewrite and a scan that refuses. |

Layer 3 imports neither of the first two. `bundle_redaction.py` imports only
`re`, `dataclasses`, `pathlib`, and `typing`; it carries its own credential
vocabulary and its own path rules. `diagnostic_bundle.py` receives the receipt,
the state report, and the connector view as plain data from its caller, and
re-projects each onto field lists it names itself.

That independence is the point rather than a style preference. A scan that
called the write-time redactor would keep passing its tests after that redactor
changed, which is exactly the failure the third layer exists to catch. Two tests
hold it: `test_the_third_layer_imports_neither_of_the_first_two` reads the
source, and `test_the_scan_still_catches_a_secret_when_the_upstream_layers_are_disabled`
replaces both upstream redactors with identity functions and asserts the export
still refuses.

## Fail closed

The scan runs twice before anything reaches the disk: once over the projected
document, and once over the exact bytes of every member, which covers whatever
the manifest and the serialisation add.

It refuses on seven categories:

| Category | Matches |
| --- | --- |
| `registered_secret` | Any value from `secrets.json`, plus any credential-named environment value this build holds. |
| `private_key` | A PEM private key header. |
| `json_web_token` | A three-segment JWT. |
| `issued_credential` | A published issuer prefix: `sk-`, `ghp_`, `github_pat_`, `glpat-`, `xox…-`, `AKIA`/`ASIA`, `AIza`, `ya29.`, `1//`, and the rest. |
| `authorization_header` | An `Authorization:` assignment, or a bare `Bearer`/`Basic` value. |
| `credential_assignment` | A named credential assigned a value, such as `api_key=…`. |
| `home_path` | An absolute path under `/Users/`, `/home/`, or `…:\Users\` that survived the path rewrite. |

A match produces `BundleScanFailed` and nothing else. The HTTP surface answers
409 with `{"error": "scan_refused", "matches": [{"category", "location"}]}`.
The matched value is never in the exception, never in the response, and never
in a log. A `location` is a dotted path into the bundle, such as
`connectors.json.connectors.0.title`.

Refusing rather than redacting is the whole design. A scan that matched means
something reached the document by a path the projection did not model. Redacting
it and continuing would ship a bundle built on a wrong assumption.

### Known limit: no entropy heuristic

The scan has no "this looks like a high-entropy secret" rule. Such a rule fires
on legitimate identifiers — a Drive file id mixes three character classes over
44 characters — and a false positive here refuses every export rather than one.
So a bare, unregistered, prefix-free secret is not caught by the scan. Two
things stand between that and a bundle: it has no field to land in, because free
text is carried as a length; and if the operator registered it, the
registered-secret rule catches it exactly. This is the same gap
`docs/run-ledger.md` records for the write path, narrowed rather than closed.

## Paths

`relativize` rewrites every string in the document before packaging:

- The state directory keeps its remainder: `<state>/club.db`. That is the
  diagnostic fact, and it is safe.
- A home-anchored path loses everything: `/Users/dana/Documents/plan.pdf`
  becomes `<home>`. The directory names and the file name identify a person and
  their work, and a reader only needs to know the path pointed outside
  Sourcecado's state.

A URL path is not a home path: `https://example.com/home/dana` is untouched.

## Determinism

`build_document` has no clock, no randomness, and no filesystem access. Two
calls against unchanged state return equal documents. Every archive member is
written with a fixed timestamp of 1980-01-01 and mode 0600, in sorted order.

The only bytes that move between two exports of one state are `manifest.json`'s
`package` object, which holds `bundle_id` and `generated_at`. `checksums.txt`
covers the evidence members and not `manifest.json`, so it too is byte-stable.

Anything clock-relative is left out rather than allowed to drift. A run's
`duration_ms` appears only when the run has finished. The health window is
bounded by a count of runs, never by a time window. Whether a process currently
holds the run's lease is not in the bundle at all.

## No partial artifact

The archive is assembled in memory and scanned there. Only complete, scanned
bytes are written, into a private temporary directory created inside the
destination — same filesystem, so the rename is atomic — and `os.replace` is the
only thing that puts a file in place. A `finally` removes the temporary
directory whatever happens. The destination directory itself is created only
after both scans pass, so a refusal leaves not even an empty folder.

`test_a_crash_after_the_temporary_archive_is_written_leaves_nothing_readable`
forces the riskiest case: it fails `os.replace` at the instant the complete
archive is on disk, asserts the staged file was real and non-empty, and then
asserts nothing readable remains anywhere under the destination.

## Inspecting a bundle offline

Sourcecado's own inspector verifies the manifest, every member checksum and
size, and every line of `checksums.txt`:

```
cd desktop
.venv/bin/python -m coworker.diagnostic_bundle inspect <path to the .zip>
```

Exit code 0 means verified, 1 means a problem was found and named. Add `--json`
for the raw report.

The archive is also verifiable with nothing but standard tools, which matters
because the person you send it to may not have Sourcecado:

```
unzip -q sourcecado-diagnostic-<bundle id>.zip -d bundle
cd bundle
shasum -a 256 -c checksums.txt
```

`checksums.txt` covers every evidence member. `manifest.json` covers everything
except itself, including `checksums.txt`, so a tampered checksum list is caught
by the inspector even though the plain-tools path cannot see it.

## Known gaps

- The presentation event log's `run_id` is the turn identity, which is still a
  different identifier space from the Agent Run id. Log records are therefore
  selected by the run's session rather than by the run itself, and
  `logs.json` carries the turn id as `turn_id` to keep the two apart. This
  closes when the write side wires the two identities together.
- Health history is a bounded window over all recent runs, not the runs related
  to this one. A person filter would be more useful and is not here.
- A bundle is written into the state directory. There is no operator-chosen save
  location, because writing outside the state root belongs to the workspace
  grant model rather than to this surface.
- `state.json` runs a full state inspection on every preview and every export.
  For a large state directory that is the slowest part of an export.
- The GUI takes a run id by hand. Opening the export from a run receipt or from
  a state finding directly is the natural next step and is not here.
