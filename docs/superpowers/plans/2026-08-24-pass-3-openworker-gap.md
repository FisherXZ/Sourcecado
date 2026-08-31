# Pass 3 — history UI + connector suite + remaining harness

Sources (read, do not import as a package):
- OpenWorker: sessions list, Gmail / Drive / Calendar descriptors, Granola as MCP OAuth (`https://mcp.granola.ai/mcp`)
- OpenClaw: gateway, SKILL.md, memory files
- Granola: official API key (Business) + MCP OAuth for assistants
- `DESIGN.md` Warm Operator: our pixels

## Gap you just named

Slices 1–18 persist **one** chat (`main.jsonl`) and one Gmail **draft** tool. Missing:

1. **Chat history management in the window** — new chat, list, open, rename. Backend already writes per-id jsonl; the UI never exposes it.
2. **A real connector suite** — Gmail beyond draft, Google Drive, Calendar, Apollo live, Granola. Not OpenWorker’s 25-app dump.

## Locked

- Interface is Warm Operator. Copy OpenWorker *jobs* (session list, connector status, inbox). Do not copy their chrome.
- One Google login, extra scopes as we add Drive/Calendar. Tokens in `secrets.json` 0600.
- Gmail **drafts and read**. No send. Same as the original Sourcecado guardrail.
- Drive: search/read first. Writes ask.
- Calendar: list is auto. Create/update asks.
- Apollo: search does not invent emails. Enrich asks. Key already in club `.env`.
- Granola: copy OpenWorker — MCP `https://mcp.granola.ai/mcp` with OAuth. API key is a fallback if Fisher has Business plan keys. Do not reverse-engineer private APIs.
- OpenWorker Cloud / Auth0 broker still out. Local loopback OAuth stays.
- Disk path stays `~/.config/club/`.

## Copy order

19. **Gmail connect actually completes.** System browser open (done). Stable `http://127.0.0.1:8765/v1/gmail/callback`. Add that URI on the Google Web client (or mint a Desktop client). Consent. Strip shows email. Alyssa Allow lands in Drafts.

20. **Chat history frontend.**
    Backend: `GET /v1/sessions`, `POST /v1/sessions` (new id), `GET /v1/sessions/{id}`, `PATCH` title. Store already keys jsonl by id — stop hardcoding `main` as the only session.
    Window: left rail (Warm Operator, ~232px): session list, New chat, click to load transcript. Title = first user line unless renamed.
    Done when: New chat blanks the transcript; old chats reopen; quit/relaunch restores the last open id.

21. **Connector panel.** One Settings/connectors surface: Gmail, Drive, Calendar, Apollo, Granola. Connected / missing / email. Never show secrets. Connect Gmail is the Google identity; Drive/Calendar request extra scopes on the same account.

22. **Gmail suite (read + draft).** Tools: `gmail_search`, `gmail_read`, `gmail_draft`. Search/read auto. Draft still asks. No `gmail_send`. Fake HTTP in pytest.

23. **Google Drive.** `drive_search`, `drive_read`. Readonly scope. Fake HTTP in pytest.

24. **Google Calendar.** `calendar_list` auto. `calendar_create` asks. Fake HTTP in pytest.

25. **Apollo live suite.** Use `APOLLO_API_KEY` already copied. Search + enrich as shipped, against real HTTP. Spend ceiling later.

26. **Granola.** OpenWorker shape: MCP server `granola` → `https://mcp.granola.ai/mcp`, OAuth connect in the connector panel. Tools show up as `mcp__granola__*`. API key path if MCP OAuth is blocked. Read notes / list meetings. Never write to Granola in v1.

27. **Scheduler runs a turn.** Due job = real turn on the open session or a dedicated run session. Asks → inbox.

28. **Warm Operator layout.** Session rail + transcript + connector/inbox strip. Cream `#FAF8F3`, avocado `#5B8C2A`, General Sans. Not a spreadsheet shell.

29. **OpenClaw MEMORY.md index** besides `{id}.md`. Sqlite still source of truth.

## Still out

- Gmail send
- Drive/Calendar/Granola destructive writes
- OpenWorker Cloud broker
- Slack/WhatsApp channels
- Teams/board
- LinkedIn/Apify
- Notion (after this suite)
- Dumping OpenWorker

## Verify

Each slice: pytest at the public seam + one click in the window. Pause unless told to keep going.

Gmail 19 is unblocked only after the Google client has redirect:

`http://127.0.0.1:8765/v1/gmail/callback`
