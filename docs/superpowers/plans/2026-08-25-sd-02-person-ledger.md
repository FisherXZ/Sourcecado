# SD-02 Connector ledger contract

Worktree: `/Users/fisher/Documents/GitHub2026/sc-spring`  
Depends: SD-01  
Spec: person file is the log from Apollo, Gmail, Calendar, Drive, web research, and meeting notes. Not an email summary.

## Goal

Given a Club tool name, arguments, and result, produce a ledger event or nothing. SD-04 will persist it. This ticket does not open sqlite and does not edit `turn.py`.

## How to build

TDD. One tool mapping per cycle. Tests call `event_from_tool` only. Fixtures are literals copied from the real tool return shapes in `gmail.py`, `drive.py`, `calendar.py`, `apollo.py`, and Granola MCP reads. Do not mock those modules.

New files: `desktop/coworker/ledger.py`, `desktop/tests/test_ledger.py`.

## Seams

| Seam | Public API | Behavior under test |
|---|---|---|
| Mapper | `event_from_tool(name, arguments, result, *, ok) -> dict \| None` | File-worthy tools become `{source, kind, summary, payload, tool}`. Everything else is `None`. |

`append_event` is not this ticket's seam. After a few mappings work, one integration test may call `PersonStore.append_event(**mapping, person_id=...)` then `timeline`, to prove the mapper dict is legal. That is optional and comes last.

## Interface

```python
def event_from_tool(
    name: str,
    arguments: dict,
    result: dict,
    *,
    ok: bool,
) -> dict | None:
    """kwargs for PersonStore.append_event except person_id / actor / session / run.
    None = do not file.
    """
```

Returned dict keys: `source`, `kind`, `summary`, `payload`, `tool`.  
`summary` is a short human sentence. Raw HTTP text belongs in `payload.detail` on failures, never in `summary`.  
Payloads never include `access_token`, `refresh_token`, `Authorization`, or API keys.

Sources already locked in SD-01: `apollo`, `gmail`, `calendar`, `drive`, `web`, `granola`, `sourcecado`.

## Vertical slices

1. **Clock and memory tools are not filed.**  
   `now`, `remember`, `memory_update`, `memory_forget`, `load_skill` → `None`.

2. **Apollo search is not filed.**  
   Candidates are not people until keep (SD-03). `apollo_search_people` → `None`.

3. **A Gmail search files as gmail / mail.**  
   Args `{query: "from:ada"}`, result `{messages: [{id, from, subject}]}`.  
   Summary names the query and the count. Payload has `query` and `ids`. No message bodies.

4. **A Gmail read files as gmail / mail.**  
   Result shape from `GmailApi.read`: `id`, `from`, `subject`, `date`.  
   Payload has those identifiers. Do not put the full `body` in the default payload.

5. **A Gmail draft files as gmail / draft with sent false.**  
   Result `{id, to, subject, drafted: True, sent: False}`.  
   Payload has `draft_id`, `to`, `subject`, `sent: False`.

6. **Drive search and read file as drive / file.**  
   Search payload: query + file ids/names. Read payload: file id + name. Not the full `content`.

7. **Calendar list / create / update file as calendar / event.**  
   List: count. Create/update: event id + summary.

8. **Apollo enrich files as apollo / enrich.**  
   Payload may include `email` (this is the point of enrich) plus name/title/company. No API key.

9. **Granola MCP reads file as granola / meeting.**  
   Any `mcp__granola__*` name that is not a write. Payload keeps id/title if present, not a raw dump.

10. **A failed Drive read files as kind error.**  
    `ok=False`, result `{error: "Drive is not connected."}`.  
    Summary is plain language. `payload.detail` has the error string.

11. **Web search and fetch have a stable shape even though Club has no tool yet.**  
    `web_search` / `web_fetch` when `ok` → `source: web`, kind `search` or `fetch`.  
    Payload: query/url plus result count or title. SD-11 must not change these keys.

12. **Secrets do not leak.**  
    If `result` contains `access_token` or a Bearer header, the returned payload does not.

## Not in this ticket

`run_turn` wiring, `people_keep`, actual web tool, GUI.
