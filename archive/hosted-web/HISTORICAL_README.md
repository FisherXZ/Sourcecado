# Sourcecado hosted web application

This was the repository README immediately before the local desktop stack became the default. It is retained to explain the archived implementation.

Sourcecado was framed as a hosted team sourcing operating system for Codeology. The implementation used Next.js 15, Postgres with pgvector, Vitest, and a browser-based Research Chat.

## Historical setup

```bash
npm install
docker compose up -d
export DATABASE_URL=postgresql://sourcecado:sourcecado@localhost:5432/sourcecado
npm run migrate
npm run dev
```

The app exposed `/api/health`, `/chat`, `/memory`, and run-inspector surfaces. Migrations in `src/migrations/` were applied in filename order. The archived `tests/` suite covered the model gateway, agent loop, tools, sourcing memory, permissions, routes, and UI.

This description is historical. Use the repository root README for the active product.
