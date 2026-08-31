# Pass 3 closure — remaining work

Work in this order. Pause after each item for a click. Do not skip ahead.

**Terms**
- Club: the local backend + window under `desktop/`
- Backend: FastAPI on `127.0.0.1:8765`
- Strip: connector/inbox/schedule row at the bottom of the window
- Tick: run due scheduler jobs now
- Full Google grant: one login that covers Gmail read+compose+send, Drive readonly, Calendar (all calendars)

**Out:** Gmail send tool, Drive writes, Calendar delete, Notion, spend ceiling. Drafts still never send even with the send grant.

---

## 1. Apollo search lives

**Done when:** Chat “search Apollo for people at Codeology” returns people with first name, obfuscated last name, title, org. No email field. Strip stays “Apollo · configured”. Enrich still asks.

**How:** Valid `APOLLO_API_KEY` in `~/.config/club/.env` (x-api-key, `mixed_people/api_search` or master). Backend loads it at boot. Current key returns 401 Invalid API key.

**Verify:** Live search from Club. `CLUB_RUN_LIVE_SMOKE=1` smoke also passes.

## 2. Scheduler + manual run

**Locked 2026-08-25 (grill):**
- `POST /v1/schedule/{id}/run` fires that job even if not due. Tick stays due-only.
- Run now does not consume the weekly slot (`next_run_at` unchanged).
- One **Run now** on the existing schedule strip.
- After a **tick**, `next_run_at` is the next Monday 09:00 America/Los_Angeles.

**Done when:**
- Strip shows the weekly job, last run, next run, Run now
- Run now uses `sched-{id}` off the rail
- Asks land in inbox
- Tick moves next run to Monday 09:00; Run now does not

**Verify:** Click Run now. See a run row. Monday slot still in the future.

## 3. Retire `send_test`

**Done when:** No schema, execute branch, ASK entry, KERNEL mention, or buddy frontmatter. Ask/deny WS tests use `gmail_draft` + FakeGmail. `decide("send_test")` is unknown/deny.

## 4. Gmail draft

**Skip.** Fisher confirmed live.

## 5. Full Google grant on every Google connector

**Done when:** Connect Gmail, Connect Drive, and Connect Calendar each request the full Google set (read + compose + send + drive.readonly + calendar). One consent can cover all three. Strip shows Gmail / Drive / Calendar connected after that consent (reload or focus). Tokens stay in `secrets.json` 0600. No send tool.

**How:** `BASE_SCOPES` already has send. Drive/Calendar extra-connect currently sends only the extra scope plus base. Make extra-connect send the same full set. Existing `calendar.events` still counts as connected until re-consent.

**Verify:** Disconnect is not required if incremental consent works. Click Connect Calendar, accept full calendar, strip stays connected. Click Connect Gmail, accept send, strip still shows email.

---

## Order and gate

1 → 2 → 3 → 5. Item 4 skipped.

Code landed 2026-08-25. Pytest 146 passed, 1 skipped.

Clicks left: Run now on the strip. Connect Gmail (then Drive/Calendar if strip still missing extra scopes) to accept send + full calendar.
