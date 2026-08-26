# TODOs

This file tracks product-level gaps only. Implementation checklists belong in dated plans under `docs/superpowers/plans/`.

## Current spring

- Build the Durable Agent Run around the existing loop using OpenWorker's prompt-driven lifecycle, question/approval suspension, partial preservation, durable resume, checkpoints, and compaction.
- Let the director select one Drive Knowledge Workspace and maintain a local read-only FTS index using the approved narrow OpenClaw memory-host design.
- Port OpenWorker's per-run scratch workspace, files/search/persistent shell/todo tools, and file-backed Artifact surface; teach common formats through Skills and add Google-native connector tools.
- Replace hard-coded connection status/tool copy with OpenWorker-style Connector Descriptors and real provider validators.
- Preserve an eval-ready Semantic Agent Trace while keeping token-stream deltas ephemeral.
- Re-run the outreach campaign and company pitch package until both finish in one Agent Run and produce accepted editable Artifacts.

The approved architecture and implementation order are recorded in
`docs/superpowers/plans/2026-08-26-evidence-reconciled-agent-os-roadmap.md`.

## Deliberately later

- Shared login, multi-operator tenancy, and hosted deployment.
- Autonomous follow-up sending or background bulk enrichment.
- LinkedIn/Apify v2.
- Formal routine/agent versioning and evaluation suites beyond the local run ledger.

## Completed transitions

- The original SQLite CLI was retired in favor of the first hosted web runtime.
- The hosted Next.js/Postgres runtime was retired from the active root and preserved under `archive/hosted-web/` on 2026-08-26.
- The local Python/React/Tauri application is now the repository default and the target of CI.
