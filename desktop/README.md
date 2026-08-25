# Club

Local sidecar and window for Fisher. One operator, one Google login, one weekly job.

This folder is the new app. The Next.js tree at the repo root is the hosted Sourcecado stack.

Keys live in `~/.config/club/.env` (never in the repo). Default model is DeepSeek V4 Pro; Kimi K3 if there is no DeepSeek key.

```
DEEPSEEK_API_KEY=...
MOONSHOT_API_KEY=...
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
APOLLO_API_KEY=...
```

Gmail drafts never send, even when the Google grant includes send. Apollo search returns first name, obfuscated last name, title, and org. No email field. Enrich still asks.

## Run

Two terminals, from `desktop/`.

Sidecar (the brain):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m coworker.run
```

Window (the remote control). Start this **after** the sidecar so Vite can read `~/.config/club/sidecar-8765.token`:

```bash
cd surfaces/gui
npm install
npm run dev
```

Open http://127.0.0.1:5180. You should see **brain reached**.

The Vite dev server proxies `/v1` to the sidecar so the browser stays same-origin. Do not point the page at `:8765` yourself.

## Tests

```bash
.venv/bin/pytest -q
```

## Native Mac window

Needs rustup (`curl https://sh.rustup.rs | sh`) and the venv above.

```bash
cd surfaces/gui
npm install
npm run tauri:dev
```

That starts Vite, spawns the sidecar, injects the token, and opens a Club window. Close hides to the menu bar. **Quit** kills the sidecar.
