# Agent evaluation harness

Status: active-stack engineering reference.

Sourcecado's baseline harness compares prompts, tool catalogs, providers/models, compaction settings, and run-budget policies through the same `run_turn` seam as the desktop sidecar. Every repetition runs in a spawned child process with a replaced minimal environment, a unique state directory and person database, connector fakes, and one exact read-write/no-shell grant for its isolated workspace. The fake lane never reads live credentials.

Run the checked-in fake baseline and candidate from the repository root:

```bash
make eval
```

This writes ignored artifacts under `desktop/.eval-artifacts/`. Evaluation artifacts may contain prompts, responses, tool arguments, and tool output. Keep them local, inspect them before sharing, and never commit them.

The artifact root, `runs/`, and every per-run directory use mode `0700`; JSON, JSONL, SQLite, and other generated files use mode `0600`. Sourcecado creates the artifact root when absent, but never chmods a caller-owned existing parent or broad repository/system root: an existing target must already be private. Artifact files are written through a `0600` same-directory temporary file and atomic replacement, and planted symlink targets are refused.

Each run keeps the native conversation and event JSONL, Sourcecado state/person databases, isolated workspace, result contract, and closed typed telemetry. Compaction retains complete assistant/tool groups or drops the whole group, and every provider input is checked for orphan results or open tool calls. Scripted tool calls must belong to the exact variant catalog. All run-local people, workspace files, and workspace directories are enumerated and compared as exact expected sets, so unintended durable effects cannot hide. Deterministic invariants cover transcript integrity, tool catalog and sequence, forbidden actions, persisted person/workspace effects, terminal state, credential-free provider/tool probes, workspace confinement, and telemetry parentage. Infrastructure errors and failed deterministic invariants make the command fail.

The report pairs baseline and candidate by scenario plus repetition. It reports pass-rate lift independently from provider-reported tokens, latency, estimated cost, retries, and compactions. Compaction occurrence is counted, but its input/output token fields remain unknown unless a future declared tokenizer or model-accounting seam measures them; character or byte lengths are never presented as token counts. Optional judge scores are observations only: a low or missing nondeterministic score does not turn a deterministic run into a failure.

## Variant controls

`EvalVariant` owns stable names plus the prompt version/text, exact active tool catalog, provider/model identity, compaction policy, and run-budget policy. Add fixed fake-provider scenarios in `coworker/evals/scenarios.py` and assert durable effects rather than prose alone.

## Live runs

Live execution is never part of `make eval`. It requires an explicit flag and an eligible provider configured in the normal Sourcecado environment:

```bash
cd desktop
.venv/bin/python -m coworker.evals --live --repetitions 1
```

The parent resolves the provider only after `--live`; the run itself receives that provider in an isolated child with no credential variables exposed to the model or tools. Live results record the actual provider, model, prompt version, and `nondeterministic: true`. They never substitute for fake-provider CI contracts or become deterministic gates because a judge happens to return a high score.
