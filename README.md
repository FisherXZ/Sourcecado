# Sourcecado

![Sourcecado: your AI sourcing director](brand/marketing/social-card-light.jpg)

Sourcecado is a local desktop assistant for Codeology sourcing directors. It
helps one operator find people, prepare tailored outreach, keep conversations
moving, and leave a useful person file for the next officer.

Chat is home. Contacts shows active sequences in Open, In conversation, and
Done. Apollo enrichment spends credits and Gmail sending reaches real people;
both require explicit approval.

## Start here

Read [CONTRIBUTING.md](CONTRIBUTING.md) to install dependencies, run with isolated
development state, and make a first contribution. It covers Python 3.14, Node 24,
and the extra tools needed for the native macOS app. You can explore the UI and
run tests without live credentials.

The usual commands run from the repository root:

```bash
make setup        # install Python and JavaScript dependencies
make backend      # start the local API
make gui          # start the browser UI in a second terminal
make test         # backend and frontend tests
make build        # frontend type check and bundle
make eval-sourcing
```

For the native window, use `make native` after stopping the separate backend and
GUI processes. Tauri starts both itself. The [packaging guide](docs/engineering/packaging.md)
covers building a distributable app.

## Repository layout

| Path | Contents |
| --- | --- |
| `backend/` | Python API, agent runtime, connectors, persistence, tests and backend packaging scripts |
| `frontend/` | React/Vite UI and tests; `src-tauri/` contains the native shell |
| `docs/` | Product and engineering references, backlog, course material and dated reviews |
| `brand/` | Brand assets and their source files |
| `archive/hosted-web/` | Retired Next.js/Postgres app; excluded from the active build |

The Python package is still named `coworker`; run it through the Makefile or from
`backend/`. Runtime data stays outside the repo. `CLUB_STATE_DIR` selects an
isolated state directory; the default is `~/.config/club/`. Keep those compatibility
names intact when changing source layout.

## Find the right reference

- [Documentation index](docs/README.md)
- [Product specification](docs/superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md)
- [Domain language](docs/domain.md)
- [Design system](docs/design.md)
- [Active and deferred work](docs/BACKLOG.md)
- [Release records](docs/releases/README.md)

[AGENTS.md](AGENTS.md) contains repository instructions for coding agents.
`CLAUDE.md` points to the same rules. Human setup and contribution instructions
live in [CONTRIBUTING.md](CONTRIBUTING.md).
