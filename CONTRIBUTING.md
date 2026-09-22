# Contributing to Sourcecado

Sourcecado helps a Codeology sourcing director find people, prepare outreach, and retain useful context.

The Python/FastAPI backend lives in `backend/`. The React/Vite UI and Tauri
shell live in `frontend/`. `archive/hosted-web/` preserves the retired application.

## Get a clean checkout running

Use macOS for native development. Install Git, Python **3.14**, Node **24**,
and npm. Native development also needs Rust via rustup and Apple's command-line
developer tools (`xcode-select --install` if absent). Docker is optional; see
[workspace authority](docs/engineering/adr/0002-sourcecado-workspace-runtime.md)
before working on shell execution.

```bash
git clone https://github.com/FisherXZ/Sourcecado.git
cd Sourcecado
git switch -c codex/my-first-change
python3.14 --version
node --version
npm --version
make setup
```

If you use nvm, run `nvm install && nvm use` before `make setup`; `.nvmrc`
selects Node 24. The Makefile selects `python3.14`, not an arbitrary `python3`
earlier on your PATH. If necessary, use
`make setup PYTHON=/absolute/path/to/python3.14`. Check the resulting interpreter
with `backend/.venv/bin/python --version`.

Setup installs the hash-locked Python dependencies and runs `npm ci`. It does
not configure credentials, migrate your normal installation, or create a
packaged app.

### Use separate development state

Keep development data outside the checkout and apart from your normal
Sourcecado installation. Choose a different directory for concurrent checkouts.
In **both terminals**, set the same absolute path:

```bash
export CLUB_STATE_DIR="$HOME/.local/state/sourcecado-dev"
```

In the first terminal, initialize it without overwriting existing credentials:

```bash
mkdir -p "$CLUB_STATE_DIR"
chmod 700 "$CLUB_STATE_DIR"
test -e "$CLUB_STATE_DIR/.env" || cp .env.example "$CLUB_STATE_DIR/.env"
chmod 600 "$CLUB_STATE_DIR/.env"
make backend
```

Wait for the backend to report that it is listening on port 8765. In the second
terminal, from the repository root:

```bash
export CLUB_STATE_DIR="$HOME/.local/state/sourcecado-dev"
make gui
```

Open [http://127.0.0.1:5180](http://127.0.0.1:5180). You should see the sourcing
conversation and navigation. The backend creates a local token; Vite reads it
at startup. If you restart the backend, restart `make gui` too, then reload.
Do not paste the token into chat, logs, or a PR.

Export `CLUB_STATE_DIR` in the shell before starting either process. Setting it
only inside the backend's `.env` does not configure Vite. Prefer an absolute
path because the two commands use different working directories.

### What works without credentials

You can open the interface, inspect empty Contacts, Skills and Settings, create
local conversations, run the test suites, build the GUI, and run deterministic
sourcing evals. An empty Contacts table is expected. Model responses require a
configured provider; an API key warning is expected if you submit chat without
one. The UI is not a simulated live assistant.

For an offline example of the complete sourcing job, run `make eval-sourcing`.
Its fake model and connectors exercise drafting, approval and person-file
effects without calling live services. Read
[the evaluation guide](docs/engineering/evaluations.md) for the scenarios and
local artifacts.

When you need live behavior, populate only the relevant entries in the isolated
`.env`: model provider keys for chat; Apollo for search/enrichment; Tavily for
web research; Google OAuth client credentials for Gmail, Drive and Calendar.
Google access also requires connecting and consenting in the app. Process
environment values take precedence over `.env`, so use a shell without unrelated
provider credentials for a credential-free check. Restart the backend after
editing `.env`, then restart Vite.

The default state directory without an override is `~/.config/club/`. The legacy
`club` name remains in environment variables, token headers and storage for
compatibility. Do not rename these as cosmetic cleanup. The launcher also reads
an ignored `backend/.env`, filling keys absent from both the environment and
the external `.env`. An empty external file alone does not guarantee a
credential-free run if a populated `backend/.env` is present. Keep credentials
in the external state directory for predictable isolation.

### Open the native window

Stop the browser-development processes first. From the repo root, with the
same `CLUB_STATE_DIR` exported:

```bash
make native
```

Tauri starts Vite and its own backend from `backend/.venv`; do not start another
`make backend`. The `tauri:dev` script supplies `tauri.dev.conf.json`, which
omits the frozen backend resource used only by packaged builds. Use
`make native` or `npm --prefix frontend run tauri:dev`, rather than bare `tauri dev`.

Closing the window hides it to the menu bar. Use Quit to stop the application
and its backend. Packaged distribution is a separate workflow:
[packaging](docs/engineering/packaging.md) and
[preview updates](docs/engineering/update-channel.md).

## Find the code for your change

Start with the [product spec](docs/superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md)
and [domain vocabulary](docs/domain.md). Then follow one behavior through the
system:

```text
React page → api.ts / chat transport → FastAPI server
           → turn loop → permission decision → tool / connector
           → person file + run ledger → events back to the UI

Tauri owns backend startup and shutdown.
Scheduled jobs share the turn loop.
Approvals and crash recovery have durable state of their own.
```

Paths below are relative to the repository root. Test names are starting points,
not exhaustive coverage.

| Change | Start reading | First relevant tests / reference |
| --- | --- | --- |
| Backend startup, auth, local state | `backend/coworker/run.py`, `server.py` | `backend/tests/test_backend_launch.py` |
| UI navigation and boot | `frontend/src/app/AppShell.tsx`, `app/sessionBootstrap.ts` | `frontend/tests/shell.test.tsx` |
| Chat streaming and restore | `frontend/src/api.ts`, `src/chat/protocol.ts`, `src/chat/store.ts` | GUI `transport.test.ts`, `store.test.ts` |
| Agent behavior and tool permission | `backend/coworker/turn.py`, `permissions.py`, `effective_tools.py`, `tools.py` | `test_chat.py`, `test_effective_tools.py`; [tools](docs/engineering/effective-tools.md) |
| Send and enrichment approval | `backend/coworker/server.py`, `gmail.py`, `inbox.py` | `test_approved_send.py`, `test_approved_enrichment.py`; [approved send](docs/engineering/approved-send.md) |
| People, sequences and evidence | `backend/coworker/people.py`, `brief.py`, `reply_filing.py` | `test_reply_filing.py`; [living brief](docs/engineering/living-brief.md) |
| Durable runs and restart | `backend/coworker/agent_run_repository.py`, `agent_run_resume.py`, `agent_run_reconcile.py` | `test_agent_run_restart.py`; [agent runs](docs/engineering/agent-runs.md) |
| Scheduling | `backend/coworker/automation/scheduler.py`, GUI `src/routes/ScheduledPage.tsx` | `test_schedule.py`, GUI `scheduledPage.test.tsx` |
| Workspace files and commands | `backend/coworker/workspace_runtime.py`, `workspace_files.py`, `workspace_shell.py` | `test_workspace_runtime.py`; [workspace ADR](docs/engineering/adr/0002-sourcecado-workspace-runtime.md) |
| Storage evolution | `backend/coworker/migrations.py`, `doctor.py`, each owning store | `test_migrations.py`, `test_doctor.py` |

The [documentation map](docs/README.md) links the other subsystem guides. The dated
[architecture review](docs/audit/2026-09-21-onboarding-architecture-review.md) explains
current boundaries and debt. The [correctness review](docs/audit/2026-09-21-onboarding-correctness-review.md)
records verified limitations; read the relevant finding before assuming a path
is complete.

## Verify a change

From the repository root:

```bash
make test
make build
make eval-sourcing
```

`make test` runs Python and GUI tests. `make build` type-checks TypeScript and
builds the browser bundle; it is not a native app build. The sourcing eval is
also a CI gate. CI additionally builds and checks the macOS artifact. Python and
GUI lint/type/coverage gates are not all in place; see
[#166](https://github.com/FisherXZ/Sourcecado/issues/166).

For fast feedback, run the smallest relevant tests first:

```bash
cd backend
.venv/bin/pytest -q tests/test_approved_send.py
```

Or, from the root:

```bash
npm --prefix frontend test -- --run tests/transport.test.ts
```

For Rust-only development checks before a frozen backend exists:

```bash
cd frontend/src-tauri
cargo fmt --check
TAURI_CONFIG='{"bundle":{"resources":[]}}' cargo test --locked
```

Release checks keep the packaged-resource requirement. Follow the packaging
guide when changing native lifecycle or distribution behavior.

## Debug a first-run problem

| Symptom | Check / next action |
| --- | --- |
| `python3.14` missing, or dependency install rejects versions | Install Python 3.14, confirm its executable, and pass `PYTHON` explicitly if necessary. A venv created by an older interpreter should be moved aside before rerunning setup. Do not edit the lockfile to accommodate the wrong Python. |
| `.env` copy says “No such file or directory” | Create the state directory first. |
| UI reports 401 or cannot connect | Confirm both terminals use the same state path, backend is on 8765, and no stale `VITE_CLUB_TOKEN` / `CLUB_API_TOKEN` overrides exist. Restart backend, then Vite. |
| Port 5180 or 8765 already in use | Stop your other development instance or quit the native app. Closing its window only hides it. |
| No model response | Configure a provider, restart, and check Settings. A successful local health response does not verify provider access. |
| Native build says `resources/sourcecado-backend` is missing | Use `make native` for development. Packaged builds need `make build-backend` first. |
| State or migration error | Run `make doctor` with the same `CLUB_STATE_DIR`; it is read-only. Read [Doctor](docs/engineering/doctor.md) before using repair. |

Browser errors are visible in developer tools and backend terminal output.
Native backend logs live under `$CLUB_STATE_DIR/logs/backend.log`. Diagnostic
bundles can include private source material even after secret redaction; inspect
them before sharing. See [diagnostics](docs/engineering/diagnostic-bundle.md).

To start over in development, stop the app and point `CLUB_STATE_DIR` at a new
empty directory. Preserve the old state until you have checked it. Do not delete
or migrate an operator's normal state to solve a setup problem.

## Make your first PR

Pick a bounded issue from the [active backlog](docs/BACKLOG.md)
with a reproducible behavior and clear acceptance criteria. Explain the expected
change, write a failing test for a bug or behavior change, then make the smallest
fix. Update the nearby module explanation and subsystem guide when their
contract changes; comments should explain an invariant or reason the code alone
does not make obvious.

Keep these boundaries intact:

- Sending binds approval to a reviewed message and recipient. No auto-send.
- Enrichment is deliberate and credit-aware. No background bulk enrichment.
- Person evidence stays attached to the correct person and run.
- An uncertain external effect is not proof of failure and must not be blindly retried.
- Secrets, local databases, transcripts and evaluation artifacts stay out of Git.

Fill the [PR template](.github/pull_request_template.md) with the user problem,
behavior change, tests run, and remaining uncertainty, including the AI
Accountability Note when applicable. Ask a teammate to review; do not merge your
own change to `main`. Course materials teach the workflow, but the product spec
and [AGENTS.md](AGENTS.md) define what the application should do.

When agent or connector behavior changes, verify the relevant live path when
authorized and available, and state any unverified behavior in the PR.
