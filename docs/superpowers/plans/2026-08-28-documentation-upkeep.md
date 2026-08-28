# Documentation Upkeep Implementation Plan

**Goal:** Make the living docs match the local desktop product, in Fisher's voice, without rewriting history.

**Architecture:** Audit-then-edit. Living docs (README, CONTEXT, DESIGN, AGENTS, CLAUDE, TODOS, CHANGELOG, desktop/*, docs/README, course, brand, PR template) get factual and discoverability fixes. Dated specs, archive, and scratchpad stay as written. Do this on a fresh branch from `main`, not on PR #141.

**Tech Stack:** Markdown in-repo. Verification is grep, link existence, and Makefile/CI cross-checks. No new generators.

**Spec:** `docs/superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md` (product). This plan is the docs-upkeep execution record. Voice: `~/.grok/skills/write-like-fisher/SKILL.md`. Workflow: `/document-release` coverage map, applied as an audit, not a page generator.

## Global Constraints

- Chat is home. The board is the operating picture. The person file is the durable domain object.
- Active runtime is `desktop/`. `archive/hosted-web/` is historical. Do not send a contributor there for setup.
- No auto-send. No background bulk enrichment. Secrets stay out of the repo.
- Do not rewrite `archive/`, `docs/archive/`, `scratchpad/`, or dated `docs/superpowers/plans/*` bodies.
- Do not clobber CHANGELOG entries. Polish or annotate only.
- Do not bump `VERSION` in this pass. Root `VERSION` is `0.2.0.0` (hosted-era). GUI/Cargo preview is `0.0.1`. Desktop product lives under CHANGELOG `[Unreleased]`. Aligning those is a product decision, not a docs cleanup.
- Do not create `ARCHITECTURE.md`. `desktop/README.md` already has the architecture diagram. Link it.
- Do not invent a first-run tutorial. Flag it as documentation debt.
- Do not include GUI/icon/CSS binaries from the dirty `claude/s7-health-diagnostics` tree. Brand masters and docs only.
- Voice: punchy default (Orwell + Hemingway). First sentence is the point. Active voice. Short words. No leverage/robust/seamless/unlock/delve. Course decks stay instructional, not academic sludge.
- Every changed line traces to a stale claim, a missing link, or a voice cut. No drive-by rewrites.

---

## Audit (already done)

Analyzed the living tree against code, CI, and Makefile on 2026-08-28. Current branch `claude/s7-health-diagnostics` / PR #141 is a health-diagnostic test change plus unrelated uncommitted brand/course/docs work.

### What is true in code

- CI Python is **3.14** (`.github/workflows/ci.yml`, `desktop/requirements.lock` generated with `--python-version 3.14`).
- Node is **24** (`.nvmrc`). That matches README.
- GUI/Cargo version is **0.0.1**. Root `VERSION` is **0.2.0.0**. CHANGELOG `[0.2.0.0]` is the hosted Next.js app from 2026-06-17.
- Makefile targets that exist and have dedicated docs: `setup`, `sidecar`, `gui`, `native`, `test`, `eval`, `eval-sourcing`, `doctor`, `doctor-repair`, `secret-scan`, `build`, `build-sidecar`, `smoke-test`, `test-update`, `update-status`, `update-rollback`, `update-manifest`, `update-verify`.
- `make native` / `tauri dev` still shells to `desktop/.venv` (ADR 0003). Packaged builds use the bundled sidecar. `packaging.md` is the how-to for the artifact.
- Rail destinations: Chat, Board, Scheduled, Connections, Skills, Memory (saved-memory review), Needs your answer (quarantine), plus Settings/workspace/update/diagnostic routes.
- Course, brand, CONTEXT-MAP, and the PR template exist on disk and are untracked.

### Coverage map

| Entity | Reference | How-to | Tutorial | Explanation |
| --- | --- | --- | --- | --- |
| Product job | spec, CONTEXT, README | README setup | none | spec "How a day feels" |
| Setup / run / test | README | README, Makefile | README is a short how-to, not a walkthrough | desktop/README |
| Board / person file | CONTEXT, spec, living-brief.md | living-brief.md, approved-send.md | none | spec |
| Approved send / enrich | approved-send.md, CONTEXT | approved-send.md | none | spec |
| Doctor | doctor.md | doctor.md, Makefile | none | doctor.md |
| Update channel | update-channel.md | update-channel.md, Makefile | none | update-channel.md |
| Packaging | packaging.md, ADR 0003 | packaging.md | none | ADR 0003 |
| Evals | evaluations.md | evaluations.md, README | none | evaluations.md |
| Workspace / shell | desktop/CONTEXT, ADR 0002 | ADR 0002 | none | ADR 0002 |
| Course | docs/course/*, CONTEXT-MAP | TICKET_TEMPLATE, PR template | COURSE_PLAN, TEACHING_DECKS | CONTEXT-MAP |
| Brand / owl | brand/README, DESIGN | `python3 desktop/packaging/make_icon.py` | none | DESIGN |
| Memory rail | GUI only | none | none | context-projection.md (partial) |
| CONTRIBUTING | missing | missing | missing | missing |
| VERSION / changelog | mismatched | none | none | CHANGELOG Unreleased vs 0.2.0.0 |

Critical gaps this plan fills: CONTRIBUTING, docs index for all `desktop/docs/`, README Python version, README discoverability, Memory named in CONTEXT, course/brand/PR template committed, ADR 0003 status, CHANGELOG era label.

Common gaps this plan flags, not fills: first-run tutorial; VERSION alignment; Memory how-to page.

### Diagram drift

`desktop/README.md` architecture diagram still matches the sidecar: Tauri/browser → `/v1` → FastAPI → providers, connectors, workspace, person files, ledger. Keep it. Do not auto-edit.

ADR 0003 header still says **Proposed** (2026-08-27) while CI `macos-preview`, `packaging.md`, `productName: Sourcecado`, and Cargo `0.0.1` show the work landed. Mark Accepted.

### Do not touch

- `archive/` and `docs/archive/` (policy already says keep them dated)
- `scratchpad/`
- `desktop/surfaces/gui/EXTERNAL_STORE_GO_NO_GO.md` body (dated proof). Index it as a dated record.
- Spec body (already the voice and product source of truth)
- GUI source, icons, CSS sitting uncommitted on PR #141
- `desktop/docs/*.md` engineering bodies except the ADR 0003 status line and any single factual contradiction found during the index pass

---

## File map

Create:

- `CONTRIBUTING.md`
- `docs/superpowers/plans/2026-08-28-documentation-upkeep.md` (this file)

Bring in (already written in the dirty tree; commit on the new branch):

- `CONTEXT-MAP.md`
- `docs/course/CONTEXT.md`
- `docs/course/COURSE_PLAN.md`
- `docs/course/TEACHING_DECKS.md`
- `docs/course/TICKET_TEMPLATE.md`
- `.github/pull_request_template.md`
- `brand/` (masters + `brand/README.md`)

Modify:

- `README.md`
- `docs/README.md`
- `desktop/README.md`
- `CONTEXT.md`
- `AGENTS.md`
- `CLAUDE.md`
- `DESIGN.md`
- `CHANGELOG.md` (annotate `[0.2.0.0]` era only)
- `TODOS.md` (only if a current-spring line is plainly done)
- `desktop/docs/adr/0003-macos-preview-artifact-packaging.md` (status line)

Leave:

- `VERSION`
- spec
- archive
- dated plans' bodies

---

### Task 1: Cut a docs branch from main

**Files:** none yet. Working tree only.

**Interfaces:**

- Consumes: `origin/main` as the base. Untracked docs/brand/course/PR-template in the current worktree.
- Produces: branch `docs/documentation-upkeep` with those files copied on, no GUI/icon/test noise from PR #141.

- [ ] **Step 1: Confirm the dirty tree still has the files to copy**

Run:

```bash
git status --short -- README.md DESIGN.md docs/README.md CONTEXT-MAP.md docs/course brand .github/pull_request_template.md
test -f CONTEXT-MAP.md && test -f docs/course/CONTEXT.md && test -f brand/README.md && test -f .github/pull_request_template.md
```

Expected: those paths exist. README/DESIGN/docs/README may be modified.

- [ ] **Step 2: Copy the docs-only files to a temp dir**

Do not `git add -A`.

```bash
mkdir -p /tmp/sc-docs/{docs,course,brand,.github}
cp README.md DESIGN.md CONTEXT-MAP.md /tmp/sc-docs/
cp docs/README.md /tmp/sc-docs/docs/
cp docs/course/*.md /tmp/sc-docs/course/
cp .github/pull_request_template.md /tmp/sc-docs/.github/
cp -R brand/. /tmp/sc-docs/brand/
```

- [ ] **Step 3: Branch from origin/main**

```bash
git fetch origin
git checkout -b docs/documentation-upkeep origin/main
```

Expected: HEAD is `origin/main`. PR #141 health tests are not on this branch.

- [ ] **Step 4: Restore the docs-only files onto the new branch**

```bash
cp /tmp/sc-docs/README.md README.md
cp /tmp/sc-docs/DESIGN.md DESIGN.md
cp /tmp/sc-docs/CONTEXT-MAP.md CONTEXT-MAP.md
cp /tmp/sc-docs/docs/README.md docs/README.md
mkdir -p docs/course .github brand
cp /tmp/sc-docs/course/*.md docs/course/
cp /tmp/sc-docs/.github/pull_request_template.md .github/pull_request_template.md
cp -R /tmp/sc-docs/brand/. brand/
```

- [ ] **Step 5: Verify the branch has no GUI/icon/test files from PR #141**

```bash
git status --short | grep -E 'desktop/tests|src-tauri/icons|GlobalRail|ThreadView|WelcomePage|shell.css|chat.css|make_icon.py' || true
```

Expected: empty.

---

### Task 2: README — facts and doors

**Files:**

- Modify: `README.md`

**Interfaces:**

- Consumes: CI Python 3.14, Makefile targets, living-doc paths, `brand/marketing/social-card-light.jpg`
- Produces: a README a new operator can follow, with doors into the rest of the docs

- [ ] **Step 1: Show the stale Python claim**

```bash
grep -n 'Python 3.13' README.md
```

Expected: a hit. That is the failing check.

- [ ] **Step 2: Replace the prerequisites and add the missing doors**

Keep the opening (product sentence + brand image). Change only what is stale or undiscoverable.

Prerequisites block becomes:

```markdown
## Prerequisites

- macOS for the native Tauri window
- Python 3.14 (CI and `desktop/requirements.lock` are pinned to 3.14)
- Node.js 24 (see `.nvmrc`)
- Rust via `rustup` for the native build only
```

After Verify, add an Operate section that links, not dumps:

```markdown
## Operate

The root `Makefile` wraps the usual commands. The long versions live next to the code:

- [Doctor](desktop/docs/doctor.md) — inspect local state. `make doctor` changes nothing. `make doctor-repair` applies only automatic repairs.
- [Secret scan](desktop/docs/secret-scan.md) — confirm a rotated credential is gone from local state without printing it. `make secret-scan`
- [Evaluations](desktop/docs/evaluations.md) — `make eval` and `make eval-sourcing`. Artifacts stay under `desktop/.eval-artifacts/` and are gitignored.
- [Packaging](desktop/docs/packaging.md) — freeze the sidecar and build `Sourcecado.app`
- [Preview updates](desktop/docs/update-channel.md) — signed manifests, drain, rollback
```

Repository map keeps `brand/`. After the map, add:

```markdown
## Docs

- [Product spec](docs/superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md) — what Sourcecado is for
- [Domain language](CONTEXT.md)
- [Design system](DESIGN.md)
- [Agent guardrails](AGENTS.md)
- [Desktop stack](desktop/README.md)
- [Documentation map](docs/README.md)
- [Course](docs/course/CONTEXT.md) — learning context, not a second product
- [How to contribute](CONTRIBUTING.md)
```

Do not list every `desktop/docs/*.md` here. That list belongs in `docs/README.md`.

- [ ] **Step 3: Re-run the Python check and a link-existence check**

```bash
grep -n 'Python 3.13' README.md && echo FAIL || echo PASS
python3 - <<'PY'
from pathlib import Path
text = Path('README.md').read_text()
need = [
    'desktop/docs/doctor.md',
    'desktop/docs/secret-scan.md',
    'desktop/docs/evaluations.md',
    'desktop/docs/packaging.md',
    'desktop/docs/update-channel.md',
    'CONTEXT.md',
    'DESIGN.md',
    'AGENTS.md',
    'desktop/README.md',
    'docs/README.md',
    'docs/course/CONTEXT.md',
    'CONTRIBUTING.md',
    'brand/marketing/social-card-light.jpg',
]
missing = [p for p in need if not Path(p).exists()]
print('missing', missing or 'none')
PY
```

Expected: `PASS` and `missing none`. `CONTRIBUTING.md` will miss until Task 3. Run this again after Task 3.

- [ ] **Step 4: Voice pass the README**

Read the whole file. Cut any sentence that restates the one before it. Keep the first sentence as the product. Do not add a CTA.

---

### Task 3: CONTRIBUTING.md

**Files:**

- Create: `CONTRIBUTING.md`

**Interfaces:**

- Consumes: README setup, Makefile, `.github/pull_request_template.md`, course Merge Gate language
- Produces: a one-screen contributor path

- [ ] **Step 1: Confirm it is missing**

```bash
test ! -f CONTRIBUTING.md && echo FAIL_MISSING || echo EXISTS
```

Expected: `FAIL_MISSING`.

- [ ] **Step 2: Write this file**

```markdown
# Contributing

Sourcecado is a local desktop assistant. Chat is home. The person file is the durable record. Send and Apollo enrichment wait for a human.

## Setup

Follow the root [README](README.md). From a clean checkout:

```bash
make setup
cp .env.example ~/.config/club/.env
make sidecar
make gui
```

Open http://127.0.0.1:5180. Fill only the credentials you will use. Never commit a populated env file, token, or database.

## Verify

```bash
make test
make build
```

`make test` is the Python sidecar suite plus GUI Vitest. `make build` type-checks and bundles the GUI. CI runs those, then the sourcing eval suite. The archived hosted app is not in CI.

If you changed agent or connector behavior, also run the relevant live path and say so in the pull request.

## Pull requests

Use [the pull-request template](.github/pull_request_template.md). A useful PR names the user problem, the behavior change, what you ran, and what you still do not know.

If AI helped, fill the AI Accountability Note. The reviewer should be able to challenge the diff without guessing.

Do not merge your own change to `main`. Do not add auto-send or background bulk enrichment. Do not treat `archive/hosted-web/` as the current product.

## Language

Product words live in [CONTEXT.md](CONTEXT.md). Course words live in [docs/course/CONTEXT.md](docs/course/CONTEXT.md). [CONTEXT-MAP.md](CONTEXT-MAP.md) says which is which. Visual work follows [DESIGN.md](DESIGN.md).
```

- [ ] **Step 3: Confirm the file exists and the README link resolves**

```bash
test -f CONTRIBUTING.md
python3 - <<'PY'
from pathlib import Path
assert Path('CONTRIBUTING.md').exists()
assert 'CONTRIBUTING.md' in Path('README.md').read_text()
print('PASS')
PY
```

Expected: `PASS`.

---

### Task 4: docs/README.md — complete the map

**Files:**

- Modify: `docs/README.md`

**Interfaces:**

- Consumes: every living doc path listed in the audit
- Produces: one map a reader can walk without grep

- [ ] **Step 1: List desktop docs that the map currently omits**

```bash
python3 - <<'PY'
from pathlib import Path
text = Path('docs/README.md').read_text()
docs = sorted(p.as_posix() for p in Path('desktop/docs').glob('*.md'))
missing = [p for p in docs if p not in text and p.split('/')[-1] not in text]
print('\n'.join(missing) or 'none')
PY
```

Expected (on current living map): most of `agent-runs.md`, `approved-send.md`, `compaction.md`, `context-projection.md`, `diagnostic-bundle.md`, `doctor.md`, `evidence-envelope.md`, `legal-artifacts.md`, `living-brief.md`, `meeting-evidence.md`, `packaging.md`, `reply-filing.md`, `run-budgets.md`, `run-ledger.md`, `secret-scan.md`, `update-channel.md`.

- [ ] **Step 2: Replace the "Current execution records" desktop slice with a full index**

Keep the existing source-of-truth and course sections (already drafted). Under execution records, list desktop docs in one table:

```markdown
## Current desktop engineering records

These describe the active local runtime. They are not the product spec. If they conflict with the 2026-08-25 specification, the spec wins.

### How the sidecar works

- [Agent runs](../desktop/docs/agent-runs.md)
- [Run ledger](../desktop/docs/run-ledger.md)
- [Run budgets](../desktop/docs/run-budgets.md)
- [Effective tools](../desktop/docs/effective-tools.md)
- [Compaction](../desktop/docs/compaction.md)
- [Context projection](../desktop/docs/context-projection.md)
- [Evidence envelope](../desktop/docs/evidence-envelope.md)
- [Evaluations](../desktop/docs/evaluations.md)

### Sourcing job seams

- [Approved send](../desktop/docs/approved-send.md)
- [Reply filing](../desktop/docs/reply-filing.md)
- [Living brief](../desktop/docs/living-brief.md)
- [Meeting evidence](../desktop/docs/meeting-evidence.md)
- [Legal artifacts](../desktop/docs/legal-artifacts.md)

### Operate and ship

- [Doctor](../desktop/docs/doctor.md)
- [Secret scan](../desktop/docs/secret-scan.md)
- [Diagnostic bundle](../desktop/docs/diagnostic-bundle.md)
- [Packaging](../desktop/docs/packaging.md)
- [Preview updates](../desktop/docs/update-channel.md)

### ADRs

- [0001 Manual run does not consume the weekly slot](../desktop/docs/adr/0001-manual-run-does-not-consume-weekly-slot.md)
- [0002 Workspace runtime](../desktop/docs/adr/0002-sourcecado-workspace-runtime.md)
- [0003 macOS preview packaging](../desktop/docs/adr/0003-macos-preview-artifact-packaging.md)

Dated plans under `superpowers/plans/` and QA under `qa/` stay execution records. Check their status against the code before treating an unchecked box as outstanding.

[DU-01 ExternalStore go/no-go](../desktop/surfaces/gui/EXTERNAL_STORE_GO_NO_GO.md) is a dated proof from 2026-08-25, not a living guide.
```

Keep archive policy as written.

- [ ] **Step 3: Re-run the omit check**

```bash
python3 - <<'PY'
from pathlib import Path
text = Path('docs/README.md').read_text()
docs = sorted(p.name for p in Path('desktop/docs').glob('*.md'))
missing = [n for n in docs if n not in text]
print('missing', missing or 'none')
PY
```

Expected: `missing none`.

---

### Task 5: desktop/README.md — stack page, not a second root README

**Files:**

- Modify: `desktop/README.md`

**Interfaces:**

- Consumes: ADR 0003 (dev uses `.venv`; release uses bundled sidecar), Python 3.14, docs index
- Produces: accurate setup for work inside `desktop/`

- [ ] **Step 1: Add a Docs pointer after Architecture**

```markdown
Engineering notes for this stack live in [desktop/docs](docs/). The map is in [docs/README.md](../docs/README.md).
```

- [ ] **Step 2: State the Python and sidecar split**

In Credentials and state, or Run, add one short paragraph:

```markdown
CI and the lockfile use Python 3.14. `make native` / `tauri dev` still start the sidecar from `desktop/.venv`. A packaged `Sourcecado.app` starts the frozen sidecar instead. See [packaging.md](docs/packaging.md).
```

Do not claim `make native` requires `make build-sidecar` for everyday dev. ADR 0003 says the opposite. `packaging.md` requires the resource path to exist for Tauri's compile-time check; if that tree is already in the checkout, leave it. If a clean `make native` fails without it, add `make build-sidecar` as a prerequisite with the error as evidence. Do not guess.

- [ ] **Step 3: Verify the paragraph is true against ADR 0003**

```bash
grep -n 'tauri dev still shells' desktop/docs/adr/0003-macos-preview-artifact-packaging.md
grep -n 'desktop/.venv' desktop/README.md
```

Expected: both hit.

---

### Task 6: CONTEXT.md and CONTEXT-MAP.md

**Files:**

- Modify: `CONTEXT.md`
- Create or keep: `CONTEXT-MAP.md` (already drafted)

**Interfaces:**

- Consumes: rail destinations in `desktop/surfaces/gui/src/app/GlobalRail.tsx` and `route.ts`
- Produces: product language that names the real supporting surfaces without making them the job

- [ ] **Step 1: Show Memory is missing from product surfaces**

```bash
grep -n 'Memory' CONTEXT.md || echo FAIL_MISSING
```

Expected: `FAIL_MISSING` or no surface definition.

- [ ] **Step 2: Extend Product surfaces, keep the job the same**

Keep Chat, Board, Person View, Scheduled Job as written. After Scheduled Job, add:

```markdown
**Memory**
The operator's saved-memory review queue. Preferences and notes about how the director works, not a person file. Unreviewed memory does not silently become sourcing fact.

**Connections, Skills, Settings**
Supporting destinations in the rail. They configure connectors, skills, workspace grants, updates, and diagnostics. They are not the job.
```

Do not rename Board. The rail label is Board. Memory is a different object.

Keep CONTEXT-MAP.md as drafted:

```markdown
# Context Map

## Contexts

- [Sourcecado Product](./CONTEXT.md) — defines the sourcing director's job, the assistant's operating language, and the durable records the product maintains
- [Sourcecado Course](./docs/course/CONTEXT.md) — defines how students learn by contributing to the product through guided and student-owned work

## Relationships

- **Course → Product**: The course uses the product's canonical language and treats the active Sourcecado repository as the shared codebase students learn to change.
- **Product → Course**: Real product needs supply bounded student work, but course terminology never changes the meaning of product concepts.
```

Voice-cut only if a sentence repeats.

- [ ] **Step 3: Verify both files exist and CONTEXT names Memory without calling it the home surface**

```bash
grep -n '^\*\*Chat\*\*' CONTEXT.md
grep -n '^\*\*Memory\*\*' CONTEXT.md
grep -n 'Course' CONTEXT-MAP.md
```

Expected: Chat still first among surfaces. Memory present. CONTEXT-MAP points at both contexts.

---

### Task 7: AGENTS.md and CLAUDE.md

**Files:**

- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`

**Interfaces:**

- Consumes: CONTEXT-MAP, CONTRIBUTING, course context, existing precedence
- Produces: agent entry points that can find course vs product

- [ ] **Step 1: Add one line to the repository map in AGENTS.md**

```markdown
- `docs/course/` — Sourcecado Course language and plan. Product language still wins. See `CONTEXT-MAP.md`.
```

Add `CONTRIBUTING.md` to documentation precedence as operating guidance, same tier as README:

Current tier 2 is `README.md`, this file, `CONTEXT.md`, and `DESIGN.md`. Change to:

```markdown
2. `README.md`, this file, `CONTEXT.md`, `DESIGN.md`, and `CONTRIBUTING.md` — current operating guidance
```

Do not demote the spec.

- [ ] **Step 2: CLAUDE.md skill routing**

Add one line under skill routing:

```markdown
- Post-ship or repo doc upkeep → `/document-release`
```

Keep the rest.

- [ ] **Step 3: Confirm precedence still starts with the spec**

```bash
grep -n '2026-08-25-sourcecado-sourcing-director-spring.md' AGENTS.md
grep -n 'document-release' CLAUDE.md
```

Expected: both hit. Spec still item 1.

---

### Task 8: DESIGN.md owl lines

**Files:**

- Modify: `DESIGN.md`

**Interfaces:**

- Consumes: `brand/README.md` mapping (flight = dock, mapping = empty thread, landing-hero = welcome, light social card = README)
- Produces: DESIGN that names the owl without turning the table into a mascot

The dirty-tree diff is the right change. Keep it:

- Memorable thing: owl with avocado satchel is mascot and dock icon. Masters in `brand/`. Owl in dock, rail mark, first-run welcome, empty states. Never on the board or dense tables. Geometric pit stays color/shape, not the app icon.
- Decisions log row dated 2026-08-28.

- [ ] **Step 1: Confirm brand files exist**

```bash
ls brand/app-icon.png brand/mascot/owl-flight.jpg brand/mascot/owl-mapping.jpg brand/mascot/owl-meadow.jpg brand/marketing/landing-hero.jpg brand/marketing/social-card-light.jpg brand/marketing/social-card-dark.jpg
```

Expected: all exist.

- [ ] **Step 2: Apply the DESIGN.md lines if the branch copy dropped them**

Match `brand/README.md`. Do not describe poses that are not in that table.

- [ ] **Step 3: Voice check**

No new metaphor. No "delightful". One paragraph, then the table row.

---

### Task 9: CHANGELOG era label, no rewrite

**Files:**

- Modify: `CHANGELOG.md`

**Interfaces:**

- Consumes: existing `[Unreleased]` and `[0.2.0.0]` entries
- Produces: a reader who can tell hosted-era 0.2.0.0 from the current desktop product

- [ ] **Step 1: Read the whole file. Do not regenerate it.**

- [ ] **Step 2: Insert one sentence under the `[0.2.0.0]` heading, before its bullets**

```markdown
## [0.2.0.0] - 2026-06-17

This entry is the hosted Next.js app, not the current desktop product. Current work is under [Unreleased].
```

Do not edit the 0.2.0.0 bullets. Do not move Unreleased into a version. Do not use the Write tool on this file.

- [ ] **Step 3: Confirm the hosted bullets still exist**

```bash
grep -n 'Next.js 15 web app shell' CHANGELOG.md
grep -n 'This entry is the hosted Next.js app' CHANGELOG.md
```

Expected: both hit.

---

### Task 10: ADR 0003 status

**Files:**

- Modify: `desktop/docs/adr/0003-macos-preview-artifact-packaging.md` line 1–8 only

**Interfaces:**

- Consumes: `.github/workflows/ci.yml` `macos-preview` job, `desktop/docs/packaging.md`, `tauri.conf.json` `productName: Sourcecado`, Cargo `version = "0.0.1"`
- Produces: ADR status that matches the tree

- [ ] **Step 1: Confirm the header still says Proposed**

```bash
grep -n 'Status: Proposed' desktop/docs/adr/0003-macos-preview-artifact-packaging.md
```

Expected: a hit.

- [ ] **Step 2: Change the status line to Accepted, keep the date, add a one-line evidence note**

```markdown
Status: Accepted — 2026-08-27 (CI `macos-preview` job, packaging how-to, and Sourcecado productName are in tree)
```

Do not rewrite the Decision body. Left-for-later (signing, DMG, Intel) stays later.

- [ ] **Step 3: Confirm Proposed is gone from the header**

```bash
sed -n '1,8p' desktop/docs/adr/0003-macos-preview-artifact-packaging.md
```

Expected: Accepted, not Proposed.

---

### Task 11: Course docs — commit, then a voice pass

**Files:**

- Modify if sludge: `docs/course/CONTEXT.md`, `docs/course/COURSE_PLAN.md`, `docs/course/TEACHING_DECKS.md`, `docs/course/TICKET_TEMPLATE.md`
- Keep: `.github/pull_request_template.md` (already matches the ticket template)

**Interfaces:**

- Consumes: course CONTEXT terms, product CONTEXT, write-like-fisher punchy register
- Produces: the same course, shorter sentences, no second product definition

- [ ] **Step 1: Confirm course claims match the active stack**

These must stay true. If a sentence says otherwise, fix it:

- students change `desktop/`, not `archive/hosted-web/`
- no Postgres, shared login, RLS, or team tenancy as current architecture
- CI is Python tests, GUI tests, typecheck/build, sourcing evals. There is no lint job in `.github/workflows/ci.yml`
- Zero-Spend Rule: Apollo enrichment is not a course prerequisite
- Guided Tickets are deferred until the course lead freezes the app (COURSE_PLAN already says this; keep it)

```bash
grep -n 'Postgres' docs/course/COURSE_PLAN.md
grep -n 'lint CI' docs/course/COURSE_PLAN.md
grep -n 'archive/hosted-web' docs/course/COURSE_PLAN.md
```

Expected: Postgres appears only as something the course does **not** claim. Lint is denied. Archive is historical.

- [ ] **Step 2: Voice pass COURSE_PLAN.md and TEACHING_DECKS.md**

Rules for this pass:

- Keep headings, week structure, slide numbering, and promises.
- Cut prefabricated phrases. Prefer "Students change Sourcecado" over "Students learn to ship a reliable AI product inside a real engineering team" when both appear. If the course promise needs one sentence, keep one, not two that say the same thing.
- Orwell i–v. No delve/robust/seamless.
- Do not invent weeks, tickets, or tools.

Work file by file. Do not merge CONTEXT into COURSE_PLAN.

- [ ] **Step 3: Confirm CONTEXT-MAP still separates the two languages**

```bash
grep -n 'never changes the meaning of product concepts' CONTEXT-MAP.md
```

Expected: hit.

---

### Task 12: TODOS.md conservative pass

**Files:**

- Modify: `TODOS.md` only if a Current-spring line is plainly false

**Interfaces:**

- Consumes: current spring list vs the spec. Code has Board, person files, approved-send, living brief. TODOS still lists those as gaps.

- [ ] **Step 1: Read TODOS.md against the spec's "What this spring is for"**

Do not mark the spring complete. The spec's end state is a director who can name a target, get people, draft, enrich on purpose, send, see the sequence, and open a successor file. TODOS can stay as remaining product gaps.

Leave the list unless a line names a thing the repo no longer is (for example "the UI has no board"). It has a board.

- [ ] **Step 2: If you change nothing, record that in the commit message body as "TODOS.md unchanged; spring gaps still open."**

Do not add new TODOS from `FIXME` comments in this pass. That is a different sweep.

---

### Task 13: Cross-doc consistency

**Files:** whatever Task 2–12 touched. No new files.

**Interfaces:**

- Consumes: the living set
- Produces: no contradiction on Python version, home surface, archive policy, VERSION

- [ ] **Step 1: Run the consistency script**

```bash
python3 - <<'PY'
from pathlib import Path
roots = [
    'README.md','AGENTS.md','CLAUDE.md','CONTEXT.md','CONTEXT-MAP.md',
    'DESIGN.md','TODOS.md','CHANGELOG.md','CONTRIBUTING.md','docs/README.md',
    'desktop/README.md','desktop/CONTEXT.md'
]
text = '\n'.join(Path(p).read_text() for p in roots if Path(p).exists())
checks = {
    'python_313_in_living': 'Python 3.13' in text,
    'hosted_as_current_setup': 'npm run dev' in Path('README.md').read_text(),
    'spec_linked': '2026-08-25-sourcecado-sourcing-director-spring.md' in Path('README.md').read_text(),
    'contributing_linked': 'CONTRIBUTING.md' in Path('README.md').read_text(),
    'course_mapped': Path('CONTEXT-MAP.md').exists(),
}
for k,v in checks.items():
    print(k, v)
PY
```

Expected:

- `python_313_in_living` False
- `hosted_as_current_setup` False (`npm run dev` may still exist in CHANGELOG 0.2.0.0; the check is README only)
- `spec_linked` True
- `contributing_linked` True
- `course_mapped` True

- [ ] **Step 2: Discoverability from README or CLAUDE.md**

Every new living file must be linked from README, docs/README, or CLAUDE.md/AGENTS.md.

```bash
python3 - <<'PY'
from pathlib import Path
entry = Path('README.md').read_text() + Path('docs/README.md').read_text() + Path('AGENTS.md').read_text() + Path('CLAUDE.md').read_text()
need = [
    'CONTRIBUTING.md','CONTEXT-MAP.md','docs/course/CONTEXT.md',
    'desktop/docs/doctor.md','desktop/docs/update-channel.md',
    'desktop/docs/packaging.md','brand/README.md'
]
for n in need:
    print(n, 'ok' if n.split('/')[-1] in entry or n in entry else 'MISSING')
PY
```

Expected: all `ok`. If `brand/README.md` is only reached via the README `brand/` map line, that counts.

- [ ] **Step 3: VERSION note, no bump**

```bash
echo "root VERSION=$(cat VERSION)"
echo "gui version=$(python3 -c 'import json; print(json.load(open(\"desktop/surfaces/gui/package.json\"))[\"version\"])')"
echo "cargo version=$(awk '/^version/{print $3; exit}' desktop/surfaces/gui/src-tauri/Cargo.toml | tr -d '"')"
```

Expected: `0.2.0.0`, `0.0.1`, `0.0.1`. CHANGELOG has the hosted-era sentence. Do not change VERSION.

---

### Task 14: Voice pass on the files this plan edited

**Files:** the living files Tasks 2–11 changed. Not archive. Not dated plans.

**Interfaces:**

- Consumes: write-like-fisher checklist
- Produces: the same facts, fewer words

Before each file is done, answer:

1. Does sentence one say the point?
2. Can this reader see what to do or what is true?
3. Is every claim in the repo?
4. Metaphor, long word, passive, jargon with a short equivalent? Cut it.
5. Would you say this out loud to Fisher, a new contributor, or a student, depending on the file?

If a rewrite still reads generated, run the humanizer skill on that file only.

Do not expand. Same length or shorter.

- [ ] **Step 1: Re-read README, CONTRIBUTING, docs/README, CONTEXT, AGENTS, CLAUDE, DESIGN, CHANGELOG annotation, course CONTEXT, COURSE_PLAN opening**

- [ ] **Step 2: Cut only sludge you introduced or that sits in a paragraph you already had to touch**

Do not restyle untouched historical bullets.

---

## Self-review

**Spec coverage**

- Product job / chat-home / person-file / no auto-send: preserved, not rewritten.
- Setup path: Task 2 + 3.
- Desktop engineering records: Task 4 + 5.
- Course as a second context: Task 6 + 11.
- Archive stays archive: global constraint.
- Voice: Task 14 and per-task cuts.

**Placeholder scan:** none. VERSION is an explicit non-goal, not a TBD.

**Named alternative:** docs branch from `main` over landing this on PR #141 because #141 is a health-diagnostic test change. VERSION left alone over silently writing `0.0.1` into root VERSION because the preview stamp and the hosted-era changelog tag are different objects.

## Documentation debt left on the table

- No first-run tutorial (Diataxis tutorial quadrant). README is the how-to.
- No Memory how-to page. CONTEXT will name it; `context-projection.md` explains the prompt slice.
- VERSION / GUI 0.0.1 / CHANGELOG Unreleased are three numbers. Product decision.
- Dated spring tickets still say "Draft" and "GUI blocked". They are execution records. Do not rewrite.
- `desktop/surfaces/gui/EXTERNAL_STORE_GO_NO_GO.md` stays a dated proof.

## Verification for the whole pass

```bash
make test
```

Docs-only. `make test` is the regression gate that this branch did not break the app, not a docs linter. The docs checks are the python/grep blocks in Tasks 2, 4, 7, 9, 13.

Do not run `/ship` from this plan. This plan ends when the files are edited and the checks pass.
