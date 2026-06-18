# Changelog

All notable changes to Sourcecado will be documented in this file.

## [0.2.0.0] - 2026-06-17

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
