# Code Quality Report — Sourcecado (`desktop/`)

> **Prepared:** 2026-08-29 · **Companion document:** `2026-08-29-sourcecado-architecture-assessment.md` · **Scope:** measured code-quality metrics — size, complexity, duplication, coverage, lint posture, dependency health — mapped to the ISO/IEC 25010 quality model. `archive/hosted-web/` excluded (historical). Sequential working notes: `2026-08-29-audit-findings-log.md`.

---

## 0. Method & Tooling (what was actually measured)

All numbers are **measured output** from tools run against the repository as committed on 2026-08-29 — not estimates.

| Metric | Tool / command | Notes |
|---|---|---|
| Size & structure | file scan (`find` + `wc -l`) | prod = `coworker/` + `packaging/`; GUI = `surfaces/gui/src` |
| Cyclomatic complexity | `ruff --select C901` at threshold 15 | count of functions exceeding CC 15 |
| Duplication | `jscpd --min-lines 5 --min-tokens 50` | run separately over backend prod and GUI src |
| Python coverage | `pytest --cov=coworker --cov=packaging` | full suite, no exclusions |
| GUI tests | `vitest run` (repo config) | coverage provider not installed; count only |
| Lint (Python) | `ruff check` — default rules (repo has **no** lint config) | plus a broader informational scan (§6) |
| Type safety (Python) | `mypy --ignore-missing-imports` (informational — repo has **no** type-checker config) | sizing the gap, not a repo gate |
| Dependency audit | `pip-audit -r requirements.lock`; `npm audit` (prod and all) | |
| Licenses | `pip-licenses` (installed env); `license-checker --production` | |

**Caveat:** Python lint/type numbers cannot use "the repo's own config" because none exists — that absence is itself finding G1.

---

## 1. Quality Scorecard (gate view)

| Metric | Measured | Typical gate | Verdict |
|---|---|---|---|
| Python line coverage | **89%** (17,356 stmts) | ≥ 70-80% | ✔ Strong |
| Test volume | 1,917 py + 520 GUI, all passing | — | ✔ Strong |
| Test/source LOC ratio | **1.16** (py), **1.10** (GUI) | ≥ 0.5 healthy | ✔ Exceptional |
| Duplication (prod) | **1.55%** py · **2.15%** GUI | < 3-5% | ✔ Pass |
| Complexity hotspots (CC>15) | **19 fns / 12 files** | minimize | ✔ Low, concentrated |
| Lint errors (ruff default) | **1** | 0 gated | ◑ Clean but **not gated** |
| TS `strict` | on (+ noUnusedLocals/Params) | on | ✔ Pass |
| ESLint | **not configured** | gated | ✘ Absent |
| Python type checking | **not configured** (86 latent mypy errors) | gated | ✘ Absent |
| Coverage gate in CI | **none** (coverage never run in CI) | floor + ratchet | ✘ Absent |
| Prod dependency vulns | **0** (npm) · **0** (pip-audit) | 0 | ✔ Pass |
| Dev dependency vulns | 5 (1 critical, 1 high — vitest 2.x chain) | trend to 0 | ◑ Upgrade available |

**Overall: A– / "Excellent, with ungated virtue."** The measured state is the cleanest of the three codebases audited in this series (compare the-hog: 3,967 lint errors, 253 CC hotspots, 812 `as any`). The debt is not in the code — it is that nothing in CI *keeps* it this way, and in a handful of very large files.

---

## 2. Size & Structure

| Metric | Backend (Python) | GUI (TS/TSX) |
|---|---|---|
| Source files | 90 | 52 |
| Source LOC | 43,549 | 13,614 |
| Test LOC | 50,585 | 15,026 |
| Test/source ratio | **1.16** | **1.10** |
| Avg LOC/file | 484 | 262 |

File-size distribution (prod Python): 0-100: **17** · 100-300: **36** · 300-500: **14** · 500-1000: **6** · 1000-2000: **16** · 2000+: **1**.

**Reading:** 59% of files are under 300 LOC, but **17 of 90 files (19%) exceed 1,000 LOC** — a far heavier tail than typical (the-hog: 1.8%). The maintainability question is outlier concentration, and the outliers are exactly the architecture seams: `server.py` 3,687 · `store.py` 1,946 · `turn.py` 1,743 · `doctor.py` 1,664 · `people.py` 1,663 · `provider.py` 1,610 · GUI `api.ts` 2,214. Mitigating context: module docstrings and per-subsystem docs are uniformly strong, so the large files are *navigable* — they are still the change-cost hotspots.

---

## 3. Cyclomatic Complexity

**19 functions exceed CC 15** across 12 files (`ruff --select C901`, threshold 15):

| File | Functions > CC 15 |
|---|---|
| `coworker/server.py` | 3 |
| `coworker/provider.py` | 3 |
| `coworker/workspace_runtime.py` | 2 |
| `coworker/doctor.py` | 2 |
| 8 further files | 1 each |

**Reading:** complexity is low in absolute terms and co-located with the god files. Enabling C901 (warn at 15) in a future ruff config would freeze the count at 19 and prevent regression for free.

---

## 4. Duplication

| Scope | Duplication | Clones |
|---|---|---|
| Backend prod (`coworker/` + `packaging/`) | **1.55%** lines (675 / 43,533) | 70 |
| GUI src | **2.15%** lines (399) | 40 |

**Reading:** both are comfortably under the 3-5% gate. GUI clones cluster in `ApprovalCard.tsx` (internal), and a shared block between `Board.tsx:127-135` and `PersonFile.tsx:182-190` — candidates for extraction during the api.ts split, not urgent.

---

## 5. Test Coverage & Test Quality

- **Python:** 1,917 passed / 3 skipped in 210s; **89% line coverage** over 17,356 statements with zero exclusions configured. Lowest: `packaging/*` scripts 0% (exercised by CI's packaged smoke test instead — a legitimate layer split), `calendar.py` 65%, `evals/runner.py` 66%.
- **GUI:** 520 tests / 58 files in 26s. Coverage not measurable with the repo config (no coverage provider installed) — count and pass-rate only.
- **Quality signals beyond volume:** invariants are asserted, not narrated — e.g. the migration-fence tests named in `desktop/docs/agent-runs.md` (`…never_commits_the_transaction…` proves non-vacuity by demonstrating `executescript` does commit); the at-most-once send guarantee is held by four mechanisms with a dedicated 20-test file (`tests/test_approved_send.py`); behavioural evals (`coworker/evals/`) run in CI on every PR.
- **The asymmetry worth knowing:** the thin `gmail_send`/enrich tool path has ~1 test asserting none of the thick path's identity properties — because those properties don't exist there (architecture AR1).

---

## 6. Code Smells & Lint Findings

**Measured with ruff default rules (E4/E7/E9/F):** **1 error** — one unused import. Auto-fixable.

**Suppression / debt markers (prod code):**

| Marker | Python | TS/TSX |
|---|---|---|
| TODO / FIXME / HACK / XXX | **0** | **0** |
| `type: ignore` / `noqa` | 1 / 2 | — |
| `as any` / `@ts-ignore` / `@ts-expect-error` | — | **0** |
| `: any` | — | 1 |

This is the cleanest marker profile in this audit series. Suppressions are the exception, not a habit.

**Informational scans (sizing what a gate would enforce — not current-config findings):**

- Broader ruff (`E,W,F,B,SIM,UP,C4`): 382 total, of which 289 are line-too-long (style) — **~93 substantive**, incl. 28 deprecated-imports, 20 suppressible-exceptions, and **4 × B023 function-uses-loop-variable (a real bug class worth checking)**. 52 auto-fixable. The 8 × B008 hits are FastAPI's default-argument idiom (false positives to configure away).
- mypy (`--ignore-missing-imports`): **86 errors in 20 of 86 files** — e.g. `evals/runner.py` attribute errors on `PersonExpectation`. The code is thoroughly type-annotated but no checker ever reads the annotations, so this class of bug is currently invisible.

---

## 7. ISO/IEC 25010 Maintainability Assessment

| Sub-characteristic | Rating | Evidence |
|---|---|---|
| Modularity | Good | clean subsystem seams, single-source policy; dragged by 17 files ≥1k LOC and one 63-route closure |
| Reusability | Good | provider protocol, event contract, store seams, workspace runtime behind typed tools |
| Analysability | **Strong** | best-in-series docstrings + per-subsystem docs; 1.55% duplication; near-zero suppressions |
| Modifiability | Moderate | change cost concentrated in server.py / api.ts / store.py; dual-path send raises modification risk on the most consequential flow |
| Testability | **Strong** | 1.16 test ratio, 89% cov, invariant-style tests, fakes-first connector design (`web.py:1` "Fake HTTP in tests") |

**Net maintainability: Good-to-Strong.** The binding constraint is file-level concentration, not code quality.

---

## 8. Governance Gaps (why this could decay)

| Gap | Evidence | Impact |
|---|---|---|
| No Python lint or type gate | no ruff/mypy config in repo; no lint step in `ci.yml` | today's 1-error state is luck-plus-culture; 86 latent mypy errors invisible |
| No ESLint | no config anywhere in `surfaces/gui` | only `tsc` stands between the GUI and drift |
| No coverage gate | CI runs `pytest -q` (ci.yml:28), never `--cov`; no threshold | 89% can regress silently |
| No npm audit in CI | pip-audit present (ci.yml:248-251); npm equivalent absent | dev-chain vulns accumulate unnoticed (5 today) |
| Dev toolchain vulns | vitest 2.1.9 → vite 5 → esbuild ≤0.24.2: 1 critical, 1 high (dev-only; prod = 0) | fix is the vitest 3.x major bump |
| Naming drift | `package.json` name "club-gui"; VERSION file vs package version 0.0.1; club.db / X-Club-Token / CLUB_* env | consistency debt; db filename is user state (architecture AR6) |
| PR template authored but uncommitted | `.github/pull_request_template.md` sits untracked in the working tree | the governance artifact exists; it just never shipped |

**Note the contrast with culture:** Rust *is* fully gated (`cargo fmt --check`, `clippy -D warnings`, ci.yml:121-130), evals run on every PR, dependencies are hash-pinned, and signing/provenance are engineered. The gap is specifically lint/type/coverage gating for Python and TS — tooling wiring, not tooling awareness.

---

## 9. Prioritized Recommendations

**Quick (hours):**
1. Add `ruff` with default rules + C901(15) + B-class to `ci.yml`; fix the 1 current error; configure B008 exception for FastAPI.
2. Add ESLint (typescript-eslint recommended-type-checked) to the GUI and CI; baseline is near-zero today, so gating is cheap *now*.
3. Add `pytest --cov` with a floor at the current 89% (or 85% for slack) to the backend job; add `npm audit --omit=dev --audit-level=high` to the GUI job.
4. Commit the PR template.

**Medium (days):**
5. Adopt mypy incrementally — start with `coworker/evals/` (where the 86 errors cluster) and the safety-critical modules (`gmail.py`, `inbox.py`, `agent_run_*`); the annotations already exist.
6. Upgrade vitest 2.1.9 → 3.x (clears all 5 dev vulns); add `@vitest/coverage-v8` and record a GUI coverage baseline.
7. Check the 4 × B023 loop-variable-capture hits — this rule finds real bugs.

**Ongoing:**
8. When touching `server.py`/`api.ts`/`store.py`, extract along the router/domain seams (architecture roadmap AR2/AR4) rather than growing them.

---

## 10. Bottom Line

The measured picture is **a rigorously tested, exceptionally clean codebase whose quality is maintained by culture rather than enforcement**: 89% coverage, 1.16 test ratio, 1.55% duplication, one lint error, zero debt markers — and no CI gate that would notice any of it regressing. The highest-leverage moves are mechanical: wire ruff/ESLint/coverage/npm-audit into the existing CI (a morning's work at today's near-zero baselines), then spend structural attention where the architecture assessment points (dual-path send, the two god seams).

---

## Appendix — Measurement Provenance

| Metric | Command (summarized) |
|---|---|
| Size | `find … -name "*.py"\|"*.ts(x)" \| xargs wc -l` per area |
| Complexity | `ruff check coworker packaging --select C901 --config "lint.mccabe.max-complexity=15"` |
| Duplication | `npx jscpd <dir> --min-lines 5 --min-tokens 50` |
| Coverage | `.venv/bin/pytest -q --cov=coworker --cov=packaging` (pytest-cov installed locally for the run) |
| GUI tests | `npm test` (vitest 2.1.9, repo config) |
| Lint | `ruff check coworker packaging` (defaults); informational: `--select E,W,F,B,SIM,UP,C4` |
| Types (informational) | `uvx mypy coworker --ignore-missing-imports` |
| Dep audit | `uvx pip-audit -r desktop/requirements.lock`; `npm audit [--omit=dev]` |
| Licenses | `uvx pip-licenses --python desktop/.venv/bin/python --summary`; `npx license-checker --production --summary` |

*All figures reflect the repository state on 2026-08-29 and drift as code evolves; recommendations #1-3 make them continuous rather than point-in-time.*
