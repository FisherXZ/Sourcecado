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
