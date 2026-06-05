# Sourcecado

SourcyAvo is a local-first sourcing memory CLI for Codeology sourcing context. It ingests exported source files, refreshes structured memory, and answers sourcing-history questions with citations, gaps, and next actions.

This MVP is single-user local memory. It does not enforce app-level permissions. Only ingest files you are allowed to use.

## Install

```bash
npm install
npm run build
```

## Commands

```bash
npm run sourcyavo -- ingest seed-data/
npm run sourcyavo -- refresh
npm run sourcyavo -- ask "Who needs follow-up for AI safety?"
```

The local SQLite database lives at `.sourcyavo/memory.db`.

## Source Formats

`ingest` accepts exported `.md`, `.txt`, `.csv`, and `.eml` files.

CSV extraction is deterministic and works locally without an API key. Markdown, text, and email refresh use the LLM extractor:

```bash
export OPENAI_API_KEY="..."
export SOURCYAVO_LLM_MODEL="<model>"
npm run sourcyavo -- refresh
```

Tests use mocked extraction and never make live model calls.

## Answer Contract

Every answer uses four sections:

- `Answer`: direct sourcing-memory synthesis.
- `Evidence`: source citations for factual claims.
- `Gaps`: missing, candidate, conflicted, or stale memory.
- `Next Action`: the next sourcing step to take.

## Local Verification

```bash
npm test
npm run build
rm -rf .sourcyavo
npm run sourcyavo -- ingest tests/fixtures/seed-data
npm run sourcyavo -- refresh
npm run sourcyavo -- ask "Who needs follow-up for AI safety?"
```

If the fixture folder includes markdown or email files, `refresh` requires `OPENAI_API_KEY` and `SOURCYAVO_LLM_MODEL`. For CSV-only local verification without LLM config, copy only CSV fixtures into a temp folder and run the same three CLI commands.
