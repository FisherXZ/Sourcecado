# H3 — CI + Vercel Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every PR to `Sourcecado` gets a red/green GitHub Actions check within
~2 minutes, and `Sourcecado` runs on Vercel against managed Supabase Postgres
with working Google sign-in.

**Architecture:** One GitHub Actions workflow runs lint + typecheck + the vitest
suite against a `pgvector/pgvector:pg16` service container — the same image
`docker-compose.yml` uses locally. CI deliberately does **not** run
`next build`; Vercel's preview deployments already build every PR, and
duplicating it would double the check time for no new signal. Hosting is a
separate axis: a Supabase project holds the production database, migrations are
applied from a workstation over the direct connection, and the Vercel runtime
connects through Supabase's transaction pooler.

**Tech Stack:** GitHub Actions, Node 24, `pgvector/pgvector:pg16`, vitest 3,
Next.js 15.5.19, Supabase Postgres, Vercel.

## Global Constraints

- **Node 24 everywhere in CI and on Vercel.** Fisher's workstation runs Node
  26.5.0, but Vercel supports only 22.x and 24.x. CI must test the runtime
  production actually runs. Node 24 over 26 because a green check on a runtime
  we never deploy is a lie. Record the version in `.nvmrc`.
- **The test suite must stay serialized.** `vitest.config.ts` sets
  `maxWorkers: 1` / `minWorkers: 1` with a comment recording that parallel
  workers race on Postgres catalog resets (80–118 flaky failures). Do not add
  `--maxWorkers` or shard the suite in CI.
- **No provider API keys in CI.** Measured 2026-08-04: with all of
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `QWEN_API_KEY`,
  `TAVILY_API_KEY`, `APOLLO_API_KEY` unset, the suite is byte-identical. The
  only live callers are two
  `it.skipIf(!process.env.SOURCECADO_RUN_LIVE_SMOKE)` gates. Never set
  `SOURCECADO_RUN_LIVE_SMOKE` in CI.
- **No auth secrets in CI either.** `.env.local` contains no `AUTH_*` or
  `GOOGLE_*` variables, and all four H1 auth suites
  (`tests/auth-{actor-isolation,allowlist,middleware-matcher,upload-isolation}.test.ts`)
  pass without them.
- **`DATABASE_URL` is the only environment variable CI needs.**
- **Tests provision their own schema.** Suites call `runMigrations(db)`
  themselves; `src/migrations/000_baseline.sql` runs
  `CREATE EXTENSION IF NOT EXISTS vector`. CI needs an empty database, not a
  seeded one.
- **Do not touch `next lint`.** It emits a deprecation notice (removed in
  Next.js 16) but exits 0 today. Migrating to the ESLint CLI is a separate
  ticket; this one does not absorb it.

## Baseline (measured 2026-08-04 on `origin/main` = `c73713c`, H1 auth merged)

| Check | Command | Result |
|---|---|---|
| Install | `npm ci` | exit 0, no native deps |
| Typecheck | `npx tsc --noEmit` | exit 0 |
| Lint | `npm run lint` | exit 0, 1 warning (unused eslint-disable in `src/lib/memory/embed.ts:23`) |
| Tests | `npm test` | 72 files, 494 passed, 2 skipped, **43.5s** |
| Build | `npm run build` | exit 0, 13 routes |

Typecheck, lint, and build were measured on `126b64f`; install and tests were
re-measured on `c73713c`. Re-run lint and build in Task 1 before trusting them.

## Auth landscape (PR #24, merged 2026-08-04 21:35 UTC)

Google sign-in is on `main`, which unblocks Task 6 and changes the deploy
posture:

- `src/middleware.ts` is a deny-by-default chokepoint. Its matcher protects
  every path except `api/auth`, `api/health`, `login`, and Next static assets,
  each anchored `(?:/|$)`. **`/api/health` stays reachable unauthenticated** —
  that is what makes the Task 5 smoke test work.
- `src/lib/auth/allowlist.ts` **fails closed in production**. With neither
  `AUTH_ALLOWED_DOMAINS` nor `AUTH_ALLOWED_EMAILS` set, `isAllowedEmail()`
  returns `env.nodeEnv !== "production"` — so on Vercel every account is
  denied, including Fisher's. Setting one of them is not optional; skipping it
  produces a deploy that looks like broken OAuth.
- Vercel Deployment Protection is therefore **not** required for safety. The
  app authenticates itself. Enable it only if you want the deployment hidden
  from the public internet entirely.

---

### Task 1: `typecheck` script + pinned Node version

CI needs a typecheck entry point (none exists) and a single source of truth for
the Node version. Folded into one task because neither is independently
reviewable.

**Files:**
- Modify: `package.json` (scripts block)
- Create: `.nvmrc`
- Test: `tests/ci-config.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `npm run typecheck` → `tsc --noEmit`, exit 0 on success. `.nvmrc`
  contains the exact string `24`. Task 2's workflow reads both.

- [ ] **Step 1: Write the failing test**

Create `tests/ci-config.test.ts`. This follows the existing
`tests/package-deps.test.ts` convention — config assertions live in the suite so
they break loudly rather than drifting.

```typescript
import { readFileSync } from "node:fs";
import { join } from "node:path";

describe("CI configuration", () => {
  const root = process.cwd();
  const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));

  it("exposes a typecheck script for CI to call", () => {
    expect(pkg.scripts).toHaveProperty("typecheck");
    expect(pkg.scripts.typecheck).toBe("tsc --noEmit");
  });

  it("pins the Node version to 24 (Vercel supports 22.x/24.x, not 26)", () => {
    const nvmrc = readFileSync(join(root, ".nvmrc"), "utf8").trim();
    expect(nvmrc).toBe("24");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- tests/ci-config.test.ts`
Expected: FAIL — first assertion errors because `pkg.scripts` has no
`typecheck` key; second errors with `ENOENT: no such file or directory, open
'.nvmrc'`.

- [ ] **Step 3: Add the script and the version file**

In `package.json`, add `typecheck` to the `scripts` block, directly after
`lint`:

```json
    "lint": "next lint",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
```

Create `.nvmrc` containing exactly one line:

```
24
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- tests/ci-config.test.ts`
Expected: PASS, 2 tests.

Then confirm the script itself works: `npm run typecheck`
Expected: exit 0, no output.

- [ ] **Step 5: Commit**

```bash
git add package.json .nvmrc tests/ci-config.test.ts
git commit -m "chore(H3): add typecheck script and pin Node 24 for CI/Vercel"
```

---

### Task 2: The CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `tests/ci-config.test.ts`

**Interfaces:**
- Consumes: `npm run typecheck` and `.nvmrc` from Task 1.
- Produces: a workflow named `CI` with one job `test`, triggered on
  `pull_request` and on `push` to `main`.

- [ ] **Step 1: Write the failing test**

Append to `tests/ci-config.test.ts`, inside the existing `describe` block. These
assertions encode the three things most likely to silently rot: the pgvector
image, the serialization constraint, and the no-live-keys rule.

```typescript
  it("runs CI against the same pgvector image as docker-compose", () => {
    const workflow = readFileSync(join(root, ".github/workflows/ci.yml"), "utf8");
    const compose = readFileSync(join(root, "docker-compose.yml"), "utf8");

    expect(workflow).toContain("pgvector/pgvector:pg16");
    expect(compose).toContain("pgvector/pgvector:pg16");
  });

  it("never enables live provider smoke tests in CI", () => {
    const workflow = readFileSync(join(root, ".github/workflows/ci.yml"), "utf8");
    // Matches a YAML assignment (`SOURCECADO_RUN_LIVE_SMOKE: ...`), not a
    // mention of the name in a comment — the workflow explains in prose why
    // the variable is deliberately absent.
    expect(workflow).not.toMatch(/^\s*SOURCECADO_RUN_LIVE_SMOKE\s*:/m);
  });

  it("does not shard or parallelize the suite (maxWorkers: 1 is deliberate)", () => {
    const workflow = readFileSync(join(root, ".github/workflows/ci.yml"), "utf8");
    // Flag forms only, for the same reason: the Test step's comment cites
    // vitest's maxWorkers setting as the justification for staying serial.
    expect(workflow).not.toContain("--shard");
    expect(workflow).not.toContain("--maxWorkers");
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- tests/ci-config.test.ts`
Expected: FAIL — three tests error with
`ENOENT: ... open '.github/workflows/ci.yml'`.

- [ ] **Step 3: Write the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  test:
    name: lint + typecheck + test
    runs-on: ubuntu-latest
    timeout-minutes: 10

    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_DB: sourcecado
          POSTGRES_USER: sourcecado
          POSTGRES_PASSWORD: sourcecado
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U sourcecado -d sourcecado"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 5

    env:
      # The only variable the suite needs. Provider keys are deliberately
      # absent: measured 2026-08-04, the suite is identical without them, and
      # the two live smoke tests stay gated behind SOURCECADO_RUN_LIVE_SMOKE.
      DATABASE_URL: postgresql://sourcecado:sourcecado@localhost:5432/sourcecado

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version-file: .nvmrc
          cache: npm

      - name: Install
        run: npm ci

      - name: Lint
        run: npm run lint

      - name: Typecheck
        run: npm run typecheck

      # Suites call runMigrations() themselves and 000_baseline.sql runs
      # CREATE EXTENSION vector, so an empty database is all that's required.
      # Runs serialized (vitest maxWorkers: 1) — ~60s locally.
      - name: Test
        run: npm test
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- tests/ci-config.test.ts`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml tests/ci-config.test.ts
git commit -m "ci(H3): lint + typecheck + vitest against pgvector service on every PR"
```

---

### Task 3: Prove CI actually runs green on GitHub

A workflow that has never executed is not a deliverable. This task ends with a
green check on a real PR.

**Files:** none — this task is verification.

**Interfaces:**
- Consumes: `.github/workflows/ci.yml` from Task 2.
- Produces: a merged (or merge-ready) PR whose `CI / lint + typecheck + test`
  check is green.

- [ ] **Step 1: Push the branch and open the PR**

```bash
git push -u origin chore/h3-deploy-ci
gh pr create --title "chore(H3): GitHub Actions CI" \
  --body "Runs lint + typecheck + the vitest suite against a pgvector service container on every PR. No provider or auth secrets required — the suite is identical without them (measured on c73713c: 72 files, 494 passed, 2 skipped, 43.5s)."
```

- [ ] **Step 2: Watch the run**

```bash
gh run watch --exit-status
```

Expected: exit 0. If it exits non-zero, get the failing step's log with
`gh run view --log-failed` before changing anything.

- [ ] **Step 3: Record the real wall-clock time**

```bash
gh run list --workflow=ci.yml --limit 1 --json displayTitle,conclusion,startedAt,updatedAt
```

If the run exceeds ~4 minutes, note where it went — the likely culprit is `npm
ci` without a warm cache, not the suite. Do not optimize preemptively; record
the number.

- [ ] **Step 4: Confirm the check is required-worthy**

Open the PR page and confirm `CI / lint + typecheck + test` appears in the
checks list. Optionally make it a required status check on `main`:

```bash
gh api -X PUT repos/FisherXZ/Sourcecado/branches/main/protection/required_status_checks \
  -f strict=true -f 'contexts[]=lint + typecheck + test'
```

**Note:** branch protection blocks *your own* merges too. This is the point —
it is the forcing function this ticket exists to build — but it is Fisher's
call. Ask before enabling.

- [ ] **Step 5: Hand off for merge**

Do not merge. Report the PR URL and the CI timing. Fisher merges his own PRs.

---

### Task 4: Supabase project + production schema

**Files:**
- Modify: `src/lib/db.ts`
- Modify: `.env.example`
- Test: `tests/db-client.test.ts`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: a reachable production `DATABASE_URL`, all 7 migrations applied,
  and `getDb()` safe to call from a Vercel serverless function.

**Why this task modifies `src/lib/db.ts`:** `getDb()` calls
`postgres(url, { onnotice: () => {} })`. postgres.js uses prepared statements by
default, and Supabase's **transaction** pooler (port 6543 — the one serverless
functions must use) does not support them. Left alone, every query from Vercel
fails at runtime. Serverless needs the transaction pooler *and* `prepare: false`
together; the alternative — the session pooler on 5432 — holds a connection per
lambda and exhausts the pool under any real concurrency.

- [ ] **Step 1: Create the Supabase project**

In the Supabase dashboard: new project, region `us-west-1` (closest to
America/Los_Angeles, which `Environment` is pinned to). Save the database
password to a password manager — **not** to any file in this repo.

From Project Settings → Database, collect both strings:
- **Direct connection** (port 5432, `db.<ref>.supabase.co`) — for migrations.
- **Transaction pooler** (port 6543, `...pooler.supabase.com`) — for Vercel.

- [ ] **Step 2: Confirm pgvector is available**

In the Supabase SQL editor:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
SELECT extversion FROM pg_extension WHERE extname = 'vector';
```

Expected: one row. If this errors, stop — the whole memory layer depends on it
and the region or plan is wrong.

- [ ] **Step 3: Write the failing test**

Add to `tests/db-client.test.ts`, inside the existing `describe("getDb()")`
block:

```typescript
  it("disables prepared statements (Supabase transaction pooler rejects them)", async () => {
    const db = getDb();
    // postgres.js exposes its resolved options; prepare must be off so the
    // same client works against pgbouncer in transaction mode on Vercel.
    expect(db.options.prepare).toBe(false);
  });
```

- [ ] **Step 4: Run test to verify it fails**

Run: `npm test -- tests/db-client.test.ts`
Expected: FAIL — `expected true to be false`, because postgres.js defaults
`prepare` to `true`.

- [ ] **Step 5: Make it pass**

In `src/lib/db.ts`, change the client construction:

```typescript
    _db = postgres(url, {
      onnotice: () => {},
      // Supabase's transaction pooler (the mode serverless functions must use)
      // does not support prepared statements. Off everywhere so local and
      // production exercise the same code path.
      prepare: false,
    });
```

- [ ] **Step 6: Run the full suite**

Run: `npm test`
Expected: 72 files, 495 passed, 2 skipped — 494 plus the one new assertion
(and +2 more files/tests if Tasks 1–2 already added `tests/ci-config.test.ts`).
If anything else fails, `prepare: false` has surfaced a real
dependency on prepared statements; investigate before proceeding.

- [ ] **Step 7: Apply migrations to Supabase**

Using the **direct** connection string (5432), not the pooler:

```bash
DATABASE_URL='postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres' npm run migrate
```

Expected: `Migrations applied.`

Verify all 7 landed:

```bash
DATABASE_URL='...direct...' npx tsx -e "
import { getDb, closeDb } from './src/lib/db.js';
const db = getDb();
console.log((await db\`SELECT name FROM schema_migrations ORDER BY name\`).map(r => r.name));
await closeDb();
"
```

Expected: `000_baseline.sql` through `006_note_provenance.sql`, 7 entries.

- [ ] **Step 8: Document the variables**

Add to `.env.example` (names and shapes only — never real values):

```
# Production (Vercel) uses Supabase's transaction pooler on port 6543.
# Migrations run against the direct connection on port 5432.
# DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-us-west-1.pooler.supabase.com:6543/postgres
```

- [ ] **Step 9: Commit**

```bash
git add src/lib/db.ts tests/db-client.test.ts .env.example
git commit -m "feat(H3): disable prepared statements for Supabase transaction pooler"
```

---

### Task 5: Vercel project + environment

**Files:**
- Modify: `README.md` (deployment section)

**Interfaces:**
- Consumes: the pooler `DATABASE_URL` from Task 4.
- Produces: a Vercel project building `main`, reachable over HTTPS.

- [ ] **Step 1: Confirm the app still authenticates itself**

PR #24 merged as `c73713c`, so `src/middleware.ts` gates every route. Confirm
it is present on the commit being deployed:

```bash
git ls-tree -r --name-only origin/main | grep -E "src/middleware.ts|src/auth.ts"
```

Expected: both paths. If either is missing, stop and enable Vercel Deployment
Protection before deploying — the server holds live OpenAI and Apollo keys, and
an unauthenticated URL is a spend and data-exposure hole.

With both present, Deployment Protection is optional. Skip it unless you want
the deployment invisible to the public internet as well as locked.

- [ ] **Step 2: Create the project**

Import `FisherXZ/Sourcecado` in the Vercel dashboard. Framework preset:
Next.js. Root directory: `./`. Build and install commands: leave as detected —
`next build` is verified green (13 routes, exit 0, no database access at build
time, so no build-time `DATABASE_URL` is needed).

Set Node.js Version to **24.x** under Project Settings → General, matching
`.nvmrc`.

- [ ] **Step 3: Set environment variables**

Project Settings → Environment Variables, scoped to **Production** and
**Preview**:

| Name | Value |
|---|---|
| `DATABASE_URL` | Supabase transaction pooler string (port 6543) |
| `OPENAI_API_KEY` | the working OpenAI key |
| `SOURCECADO_GENERATION_PROVIDER` | `openai` |
| `SOURCECADO_GENERATION_MODEL` | same value as `.env.local` |
| `SOURCECADO_EMBEDDING_MODEL` | same value as `.env.local` |
| `SOURCECADO_EMBEDDING_DIMENSIONS` | same value as `.env.local` |
| `APOLLO_API_KEY` | the Apollo key |
| `GOOGLE_CLIENT_ID` | from the Google OAuth client |
| `GOOGLE_CLIENT_SECRET` | from the Google OAuth client |
| `AUTH_SECRET` | fresh value — `openssl rand -base64 32`, not the local one |
| `AUTH_URL` | `https://<project>.vercel.app` |
| `AUTH_ALLOWED_DOMAINS` | `berkeley.edu` (or set `AUTH_ALLOWED_EMAILS` instead) |

`AUTH_ALLOWED_DOMAINS` / `AUTH_ALLOWED_EMAILS`: **at least one is mandatory.**
`isAllowedEmail()` denies every account in production when both are empty.

Do **not** set `ANTHROPIC_API_KEY` (billing-blocked), `TAVILY_API_KEY` (over
quota), `DEEPSEEK_API_KEY` (empty), or `SOURCECADO_RUN_LIVE_SMOKE`. Read every
value out of `.env.local` — do not retype from memory, and do not paste any of
them into a commit, a log, or a chat message.

- [ ] **Step 4: Deploy and smoke-test the health endpoint**

Trigger a production deploy from the dashboard, then:

```bash
curl -sS https://<project>.vercel.app/api/health
```

Expected: `{"status":"ok"}`. (If Deployment Protection is on, this returns the
Vercel auth challenge instead — that is a pass for this step. Verify through the
browser while signed in to Vercel.)

- [ ] **Step 5: Verify the database connection from production**

Open `/memory` in a browser. Expected: the page renders its source list without
a 500. This is the first real exercise of the pooler string plus
`prepare: false` — a `prepared statement already exists` error here means Task 4
Step 5 did not take effect in the deployed build.

- [ ] **Step 6: Document it**

Add a `## Deployment` section to `README.md` recording: the Vercel project name,
that production runs on Supabase, that migrations are applied manually from a
workstation over the direct connection (there is no deploy-time migration step),
and that the runtime uses the transaction pooler. Names and URLs only — no
credentials.

- [ ] **Step 7: Commit**

```bash
git add README.md
git commit -m "docs(H3): record Vercel + Supabase deployment topology"
```

---

### Task 6: End-to-end verification — sign in and chat

**Unblocked** — PR #24 merged as `c73713c` on 2026-08-04.

**Files:** none — this task is verification.

**Interfaces:**
- Consumes: the deployed app from Task 5.
- Produces: the confirmed success criterion — sign in, ask, get a cited answer.

- [ ] **Step 1: Register the OAuth redirect URI**

In Google Cloud Console → Credentials → the OAuth 2.0 Client used by H1, add:

```
https://<project>.vercel.app/api/auth/callback/google
```

Preview deployments get generated hostnames that will **not** match. Either add
a stable preview domain or accept that sign-in works on production only —
document whichever you choose in the README's Deployment section.

- [ ] **Step 2: Confirm the allowlist admits you**

The five auth variables were set in Task 5 Step 3. The one that silently breaks
sign-in is the allowlist, so verify it directly rather than by inspection:

```bash
npx tsx -e "
import { isAllowedEmail } from './src/lib/auth/allowlist.js';
console.log(isAllowedEmail('fisherxz@berkeley.edu', {
  allowedDomains: 'berkeley.edu', allowedEmails: '', nodeEnv: 'production',
}));
"
```

Expected: `true`. Then confirm the same variable is actually set in Vercel for
**Production** — a value present only in Preview leaves production denying
everyone.

- [ ] **Step 3: Sign in**

In a fresh private browser window, open `https://<project>.vercel.app`, sign in
with Google. Expected: redirected back signed in, with the sign-out control
visible in the app shell.

- [ ] **Step 4: Run the actual success criterion**

Open `/chat` and ask a question answerable from memory. Expected: a streamed
answer with source citations, and no 500s in the Vercel function logs.

Production memory starts empty, so seed one source first via `/memory` import —
otherwise a correct "I don't have anything on that" reads like a failure.

- [ ] **Step 5: Check the run ledger recorded it**

```bash
DATABASE_URL='...direct...' npx tsx -e "
import { getDb, closeDb } from './src/lib/db.js';
const db = getDb();
console.log(await db\`SELECT id, actor, created_at FROM runs ORDER BY created_at DESC LIMIT 3\`);
await closeDb();
"
```

Expected: at least one row, with `actor` set to the signed-in Google identity
rather than the default actor. That proves H1's per-user attribution survived
the deploy.

- [ ] **Step 6: Report**

Report to Fisher: the production URL, whether Deployment Protection is still on,
CI timing from Task 3, and anything deferred.

---

## Self-Review

**Spec coverage.** CI → Tasks 1–3. Managed pgvector → Task 4. Secrets → Task 5
Step 3 and Task 6 Step 2. Deploy → Task 5. Working sign-in → Task 6. The
confirmed out-of-scope list (fork-safe CI, H4 error monitoring, live smoke tests
in CI, deploys gated on CI, preview databases) has no tasks, correctly.

**Known gaps, deliberate:**
- No automatic migration step on deploy. Migrations are manual from a
  workstation. A schema change shipped without running them breaks production
  silently. Acceptable at one operator; revisit before anyone else deploys.
- Preview deployments share the production database. With no preview-scoped
  database, a preview branch writes to production data. Called out here rather
  than solved — solving it means a second Supabase project and a Vercel
  environment split, which is its own ticket.
- `next lint` remains deprecated. Left alone per Global Constraints.

**Type consistency.** `getDb()` / `closeDb()` / `runMigrations(db)` match
`src/lib/db.ts` and `src/lib/migrate.ts` as read on `126b64f`. The
`npm run typecheck` script name is used identically in Tasks 1 and 2. The
`.nvmrc` value `24` is asserted in Task 1 and consumed by `node-version-file` in
Task 2.
