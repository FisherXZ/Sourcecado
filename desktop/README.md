# Sourcecado desktop

This is Sourcecado's active application: a local Python sidecar plus a React/Vite interface packaged as a Tauri macOS app.

The root [README](../README.md) is the canonical setup guide. This document covers the stack inside `desktop/`.

## Architecture

```text
Tauri or browser window
        │
        │ authenticated local HTTP (/v1)
        ▼
FastAPI sidecar (coworker/)
        │
        ├── model provider and tool loop
        ├── Apollo, Gmail, Drive, Calendar, Granola, and web connectors
        ├── permissions and approval gates
        └── local conversations, person files, schedules, and run ledger
```

The Vite development server proxies `/v1` to the sidecar so browser development stays same-origin. Do not hard-code the sidecar port in UI code. The native shell starts and stops the sidecar, injects the API token, and hides to the menu bar on window close.

## Credentials and state

Copy the root `.env.example` to `~/.config/club/.env`. Existing process environment variables win over values from that file.

The current model preference is DeepSeek V4 Pro with Kimi K3 as fallback. Google OAuth enables Gmail, Drive, and Calendar. Apollo enrichment and Gmail sending remain explicit, approval-gated actions.

The default local state directory is `~/.config/club/`. Set `CLUB_STATE_DIR` for isolated development or tests. Never commit credentials, API tokens, OAuth grants, or local databases.

## Run from this directory

Sidecar:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m coworker.run
```

GUI, after the sidecar is running:

```bash
cd surfaces/gui
npm ci
npm run dev
```

Open [http://127.0.0.1:5180](http://127.0.0.1:5180).

Native window:

```bash
cd surfaces/gui
npm run tauri:dev
```

## Verify

```bash
.venv/bin/pytest -q
cd surfaces/gui
npm test
npm run build
```

The root `Makefile` wraps these commands for normal use.
