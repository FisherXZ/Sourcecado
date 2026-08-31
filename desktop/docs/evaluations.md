# Agent evaluation harness

Status: active-stack engineering reference.

Sourcecado's baseline harness compares prompts, tool catalogs, providers/models, compaction settings, and run-budget policies through the same `run_turn` seam as the desktop backend. Every repetition runs in a spawned child process with a replaced minimal environment, a unique state directory and person database, connector fakes, and one exact read-write/no-shell grant for its isolated workspace. The fake lane never reads live credentials.

Run the checked-in fake baseline and candidate from the repository root:

```bash
make eval
```

This writes ignored artifacts under `desktop/.eval-artifacts/`. Evaluation artifacts may contain prompts, responses, tool arguments, and tool output. Keep them local, inspect them before sharing, and never commit them.

The artifact root, `runs/`, and every per-run directory use mode `0700`; JSON, JSONL, SQLite, and other generated files use mode `0600`. Sourcecado creates the artifact root when absent, but never chmods a caller-owned existing parent or broad repository/system root: an existing target must already be private. Artifact files are written through a `0600` same-directory temporary file and atomic replacement, and planted symlink targets are refused.

Each run keeps the native conversation and event JSONL, Sourcecado state/person databases, isolated workspace, result contract, and closed typed telemetry. Compaction retains complete assistant/tool groups or drops the whole group, and every provider input is checked for orphan results or open tool calls. Scripted tool calls must belong to the exact variant catalog. All run-local people, workspace files, and workspace directories are enumerated and compared as exact expected sets, so unintended durable effects cannot hide. Deterministic invariants cover transcript integrity, tool catalog and sequence, forbidden actions, persisted person/workspace effects, terminal state, credential-free provider/tool probes, workspace confinement, and telemetry parentage. Infrastructure errors and failed deterministic invariants make the command fail.

The report pairs baseline and candidate by scenario plus repetition. It reports pass-rate lift independently from provider-reported tokens, latency, estimated cost, retries, and compactions. Compaction occurrence is counted, but its input/output token fields remain unknown unless a future declared tokenizer or model-accounting seam measures them; character or byte lengths are never presented as token counts. Optional judge scores are observations only: a low or missing nondeterministic score does not turn a deterministic run into a failure.

## Behavioural sourcing scenes

The sourcing suite runs complete sourcing-director scenes against the shipped
prompt, the effective tool catalog, and the runtime permission policy. Run it
from the repository root:

```bash
make eval-sourcing
```

or directly:

```bash
cd desktop
.venv/bin/python -m coworker.evals --suite sourcing --artifacts .eval-artifacts
```

The same command runs in the Python backend CI job after `pytest -q`, so a
behavioural regression fails the pull request. `desktop/tests/test_sourcing_evals.py`
runs every scene again inside pytest and adds the non-vacuity checks.

Nine positive scenes cover target intake, an Apollo shortlist, drafting from
existing person-file fields, deliberate enrichment, an approved send, a
follow-up after a real reply, conflicting evidence, a meeting brief, and a
successor handoff. Six negative scenes cover an invented target, bulk
enrichment, auto-send, cross-person memory bleed, a stale source claim, and a
hallucinated tool capability. In a negative scene the scripted model attempts
the unwanted action and the assertion is that the runtime produced no durable
effect.

Every scene asserts five observable dimensions: the deliverable, the tool
sequence, the approval behaviour, the persisted person-file effect, and whether
a filed claim carried a source reference or a named knowledge gap. The approval
dimension uses an ordered event ledger built from `permission_required`,
`tool_started`, `tool_finished`, and `approval_resolved`, so "approval was
requested before the effect" is checked as an ordering fact rather than an
inference. The evidence dimension cross-checks every filed `artifact` against
the `source_ref` records on the same person file, so an artifact cannot cite a
source the person file does not hold.

No assertion reads prompt prose. `EvalVariant` carries the assembled
`sourcing-director-v1` text and the run records its version, character count,
and SHA-256, but nothing matches on wording. `test_rewording_the_prompt_does_not_change_any_scene_outcome`
replays every scene under a differently worded prompt and requires the suite to
stay green, so a harmless reword cannot break the gate while a dropped approval
gate still does.

Each run records the contract it used in `run_contract`: the prompt version and
fingerprint, the effective tool catalog with each tool's runtime approval class,
the forbidden tools, the approval answers, and the observed event ledger.

Scenes get their offline inputs from `ConnectorFixtures`: Apollo and Tavily keys
that are literal fakes unlocking an in-process route table, seeded Gmail drafts
and messages, and `SeedPerson` records built through the real `PersonStore` API.
Scripted tool calls address run-created records through `$EVAL_PERSON:<apollo id>`
and `$EVAL_VERSION:<apollo id>` placeholders, resolved at call time so
version-checked writes stay deterministic.

## Variant controls

`EvalVariant` owns stable names plus the prompt version/text, exact active tool catalog, provider/model identity, compaction policy, and run-budget policy. Add fixed fake-provider scenarios in `coworker/evals/scenarios.py` and assert durable effects rather than prose alone.

## Live runs

Live execution is never part of `make eval` or `make eval-sourcing`. It requires an explicit flag and an eligible provider configured in the normal Sourcecado environment:

```bash
cd desktop
.venv/bin/python -m coworker.evals --live --repetitions 1
```

The parent resolves the provider only after `--live`; the run itself receives that provider in an isolated child with no credential variables exposed to the model or tools. `--live --suite sourcing` is refused: the sourcing scenes assert exact deterministic ledgers, so a live model cannot be scored against them and a live run can never stand in for the offline gate. Live results record the actual provider, model, prompt version, and `nondeterministic: true`. They never substitute for fake-provider CI contracts or become deterministic gates because a judge happens to return a high score.
