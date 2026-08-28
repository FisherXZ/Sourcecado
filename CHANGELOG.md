# Changelog

All notable changes to Sourcecado will be documented in this file.

## [Unreleased]

### Added
- Local Python/FastAPI sidecar with a provider-independent tool loop, permissions, schedules, connector support, person files, and a durable run ledger
- React/Vite desktop workspace with chat, board, person, connections, schedules, skills, settings, recovery, and structured sourcing results
- Tauri macOS shell that owns the sidecar lifecycle and local API token
- Root `Makefile`, active-stack CI, local environment template, and documentation map

### Changed
- Sourcecado's current product is now the local, one-operator sourcing-director assistant under `desktop/`
- Chat is the home surface; the board is the operating picture and the person file is the durable record
- Gmail moved from drafts-only to explicit review-and-send; automatic sending remains prohibited
- Default CI now verifies the Python sidecar and React GUI rather than the retired hosted application

### Archived
- The former Next.js/Postgres/pgvector implementation moved intact to `archive/hosted-web/` for historical reference
- Hosted-team, weekly-ranking, and older runtime plans remain dated historical records unless the current product specification adopts them

## [0.2.0.0] - 2026-06-17

This entry is the hosted Next.js app, not the current desktop product. Current work is under [Unreleased].

### Added
- Next.js 15 web app shell replacing the CLI-only interface — open `npm run dev` to launch the browser app
- `/chat` route with a Research Chat placeholder page — ready to wire up to sourcing memory
- `/api/health` endpoint returning `{ status: "ok" }` — monitoring tools can ping this to confirm the app is up
- SourcyAvo nav bar and home page with a link to Research Chat
- Tailwind CSS v4 styling with Geist font
- Test for the health endpoint (TDD red→green)
- `TODOS.md` tracking the pre-existing CLI native-build issue

### Changed
- `tsconfig.json` updated to Next.js-compatible config (bundler module resolution, `noEmit`, JSX preserve)
- `vitest.config.ts` isolated test TypeScript types into `tsconfig.test.json` — `vitest/globals` no longer bleeds into production scope
- `package.json` merged Next.js + React dependencies alongside existing CLI deps

### Removed
- Dead `sourcyavo` npm script that pointed to a `dist/` directory that no longer gets built
