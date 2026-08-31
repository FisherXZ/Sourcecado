# Sourcecado

![Sourcecado — Your AI sourcing director.](brand/marketing/social-card-light.jpg)

Sourcecado is a local-first desktop assistant for Codeology sourcing directors. It helps one operator find people, prepare tailored outreach, keep active conversations moving, and leave behind a useful person file for the next officer.

The current product is the Python backend and React/Tauri desktop app in `desktop/`. The retired hosted Next.js implementation is preserved under `archive/hosted-web/`; it is historical reference, not the default runtime.

## Current product

- Chat
- People enrichment: Apollo supplies name, email, phone ; enrichment is deliberate because it spends credits.
- Connectors: Gmail, Drive, Calendar, Granola, and web research add context
- Contacts: a compact table of active sequences in Open, In conversation, and Done. Kept people without a sequence remain durable Person Files but do not appear in Contacts yet.

The current product specification is [Sourcecado as the sourcing director's assistant](docs/superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md).

## Prerequisites

- macOS for the native Tauri window
- Python 3.14 (CI and `desktop/requirements.lock` are pinned to 3.14)
- Node.js 24 (see `.nvmrc`)
- Rust via `rustup` for the native build only

## Set up

From the repository root:

```bash
make setup
cp .env.example ~/.config/club/.env
```

Fill in the credentials you intend to use. Sourcecado never reads credentials from a committed repository file.

## Run

Browser development uses two terminals:

```bash
make backend
```

```bash
make gui
```

Open [http://127.0.0.1:5180](http://127.0.0.1:5180). Start the backend first so Vite can read the local API token.

For the native macOS window:

```bash
make native
```

## Verify

```bash
make test
make build
```

`make test` runs the Python backend suite and the GUI Vitest suite. `make build` type-checks and bundles the GUI. CI runs the same active-stack checks; the archived hosted app is intentionally excluded.

Run the deterministic baseline/candidate agent harness separately with `make eval`. It writes ignored, potentially sensitive local artifacts under `desktop/.eval-artifacts/`; see [the evaluation harness guide](desktop/docs/evaluations.md).

## Operate

The root `Makefile` wraps the usual commands. The long versions live next to the code:

- [Doctor](desktop/docs/doctor.md) — inspect local state. `make doctor` changes nothing. `make doctor-repair` applies only automatic repairs.
- [Secret scan](desktop/docs/secret-scan.md) — confirm a rotated credential is gone from local state without printing it. `make secret-scan`
- [Evaluations](desktop/docs/evaluations.md) — `make eval` and `make eval-sourcing`. Artifacts stay under `desktop/.eval-artifacts/` and are gitignored.
- [Packaging](desktop/docs/packaging.md) — freeze the backend and build `Sourcecado.app`
- [Preview updates](desktop/docs/update-channel.md) — signed manifests, drain, rollback

## Repository map

- `desktop/coworker/` — local FastAPI backend, agent loop, tools, connectors, permissions, and persistence
- `desktop/surfaces/gui/` — React/Vite interface and Tauri shell
- `desktop/tests/` — Python tests
- `brand/` — mascot poses, landing hero, social cards, and the dock-icon master
- `docs/` — current product records, ADRs, QA evidence, plans, and historical design documents
- `archive/hosted-web/` — retired Next.js/Postgres implementation, kept intact for reference
- `scratchpad/` — non-authoritative working artifacts

## Docs

- [Product spec](docs/superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md) — what Sourcecado is for
- [Domain language](CONTEXT.md)
- [Design system](DESIGN.md)
- [Agent guardrails](AGENTS.md)
- [Desktop stack](desktop/README.md)
- [Documentation map](docs/README.md)
- [Course](docs/course/CONTEXT.md) — learning context, not a second product
- [How to contribute](CONTRIBUTING.md)

See [archive/README.md](archive/README.md) for archive policy.

## Local state

Runtime data and secrets are deliberately outside source control:

- `~/.config/club/.env` — provider and connector credentials
- `~/.config/club/` — backend token and default local state
- `CLUB_STATE_DIR` — optional state-directory override for tests or isolated runs

Do not delete or migrate local state as part of repository cleanup without an explicit backup and migration plan.
