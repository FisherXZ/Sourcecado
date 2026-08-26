from coworker.ledger import event_from_tool


def test_clock_and_memory_tools_are_not_filed():
    for name in (
        "now",
        "remember",
        "memory_update",
        "memory_forget",
        "load_skill",
    ):
        assert event_from_tool(name, {}, {"ok": True}, ok=True) is None


def test_apollo_search_is_not_filed():
    event = event_from_tool(
        "apollo_search_people",
        {"organizationName": "Codeology", "personTitles": ["Partner"]},
        {
            "people": [
                {
                    "apolloId": "abc123",
                    "firstName": "Alyssa",
                    "lastNameObfuscated": "W***n",
                    "title": "Partner",
                    "organizationName": "Codeology",
                    "hasEmail": True,
                }
            ]
        },
        ok=True,
    )
    assert event is None


def test_gmail_search_files_as_mail():
    event = event_from_tool(
        "gmail_search",
        {"query": "from:ada"},
        {
            "messages": [
                {
                    "id": "m1",
                    "from": "ada@analytic.example",
                    "subject": "Hello",
                }
            ]
        },
        ok=True,
    )
    assert event is not None
    assert event["source"] == "gmail"
    assert event["kind"] == "mail"
    assert event["tool"] == "gmail_search"
    assert "from:ada" in event["summary"]
    assert "1" in event["summary"]
    assert event["payload"]["query"] == "from:ada"
    assert event["payload"]["ids"] == ["m1"]
    assert "body" not in event["payload"]
    assert "messages" not in event["payload"]


def test_gmail_read_files_as_mail_without_body():
    event = event_from_tool(
        "gmail_read",
        {"message_id": "m1"},
        {
            "id": "m1",
            "from": "ada@analytic.example",
            "to": "fisher@example.com",
            "subject": "Hello",
            "date": "Mon, 1 Jan 2026",
            "snippet": "Hi Fisher",
            "body": "Hi Fisher, long body that must not be filed.",
            "sent": False,
        },
        ok=True,
    )
    assert event is not None
    assert event["source"] == "gmail"
    assert event["kind"] == "mail"
    assert event["tool"] == "gmail_read"
    assert event["payload"] == {
        "id": "m1",
        "from": "ada@analytic.example",
        "subject": "Hello",
        "date": "Mon, 1 Jan 2026",
    }
    assert "body" not in event["payload"]


def test_gmail_draft_files_as_unsent_draft():
    event = event_from_tool(
        "gmail_draft",
        {
            "to": "ada@analytic.example",
            "subject": "Dinner",
            "body": "Would you join?",
        },
        {
            "id": "draft_1",
            "to": "ada@analytic.example",
            "subject": "Dinner",
            "drafted": True,
            "sent": False,
        },
        ok=True,
    )
    assert event is not None
    assert event["source"] == "gmail"
    assert event["kind"] == "draft"
    assert event["tool"] == "gmail_draft"
    assert event["payload"] == {
        "draft_id": "draft_1",
        "to": "ada@analytic.example",
        "subject": "Dinner",
        "sent": False,
    }


def test_drive_search_and_read_file_as_file():
    search = event_from_tool(
        "drive_search",
        {"query": "research dinner"},
        {
            "files": [
                {"id": "d1", "name": "Dinner notes", "mimeType": "application/pdf"},
                {"id": "d2", "name": "Deck"},
            ]
        },
        ok=True,
    )
    assert search is not None
    assert search["source"] == "drive"
    assert search["kind"] == "file"
    assert search["tool"] == "drive_search"
    assert search["payload"]["query"] == "research dinner"
    assert search["payload"]["files"] == [
        {"id": "d1", "name": "Dinner notes"},
        {"id": "d2", "name": "Deck"},
    ]
    read = event_from_tool(
        "drive_read",
        {"file_id": "d1"},
        {
            "id": "d1",
            "name": "Dinner notes",
            "mimeType": "application/pdf",
            "content": "secret file body",
            "truncated": False,
        },
        ok=True,
    )
    assert read is not None
    assert read["source"] == "drive"
    assert read["kind"] == "file"
    assert read["payload"] == {"id": "d1", "name": "Dinner notes"}
    assert "content" not in read["payload"]


def test_calendar_list_create_update_file_as_event():
    listed = event_from_tool(
        "calendar_list",
        {},
        {
            "events": [
                {"id": "e1", "summary": "Catch up"},
                {"id": "e2", "summary": "Dinner"},
            ]
        },
        ok=True,
    )
    assert listed is not None
    assert listed["source"] == "calendar"
    assert listed["kind"] == "event"
    assert listed["tool"] == "calendar_list"
    assert listed["payload"]["count"] == 2
    created = event_from_tool(
        "calendar_create",
        {"summary": "Dinner", "start": "2026-09-01T18:00:00", "end": "2026-09-01T19:00:00"},
        {"id": "e3", "summary": "Dinner", "htmlLink": "https://calendar.google.com/event?eid=e3"},
        ok=True,
    )
    assert created is not None
    assert created["payload"] == {"id": "e3", "summary": "Dinner"}
    updated = event_from_tool(
        "calendar_update",
        {"event_id": "e3", "summary": "Dinner (moved)"},
        {"id": "e3", "summary": "Dinner (moved)"},
        ok=True,
    )
    assert updated is not None
    assert updated["kind"] == "event"
    assert updated["payload"] == {"id": "e3", "summary": "Dinner (moved)"}


def test_apollo_enrich_files_email_without_api_key():
    event = event_from_tool(
        "apollo_enrich_contact",
        {
            "firstName": "Alyssa",
            "lastName": "Wilson",
            "organizationName": "Codeology",
        },
        {
            "name": "Alyssa Wilson",
            "title": "Partner",
            "organizationName": "Codeology",
            "linkedinUrl": "https://linkedin.com/in/alyssa",
            "email": "alyssa@codeology.example",
            "phone": None,
        },
        ok=True,
    )
    assert event is not None
    assert event["source"] == "apollo"
    assert event["kind"] == "enrich"
    assert event["tool"] == "apollo_enrich_contact"
    assert event["payload"]["email"] == "alyssa@codeology.example"
    assert event["payload"]["name"] == "Alyssa Wilson"
    assert event["payload"]["title"] == "Partner"
    assert event["payload"]["organizationName"] == "Codeology"
    assert "api_key" not in event["payload"]


def test_granola_mcp_read_files_as_meeting():
    event = event_from_tool(
        "mcp__granola__get_note",
        {"id": "n1"},
        {"id": "n1", "title": "Catch up with Ada", "transcript": "long note body"},
        ok=True,
    )
    assert event is not None
    assert event["source"] == "granola"
    assert event["kind"] == "meeting"
    assert event["tool"] == "mcp__granola__get_note"
    assert event["payload"] == {"id": "n1", "title": "Catch up with Ada"}
    assert "transcript" not in event["payload"]
    assert (
        event_from_tool(
            "mcp__granola__create_note",
            {"title": "nope"},
            {"id": "n2"},
            ok=True,
        )
        is None
    )


def test_failed_drive_read_files_as_error():
    event = event_from_tool(
        "drive_read",
        {"file_id": "d1"},
        {"error": "Drive is not connected."},
        ok=False,
    )
    assert event is not None
    assert event["source"] == "drive"
    assert event["kind"] == "error"
    assert event["tool"] == "drive_read"
    assert "Drive" in event["summary"]
    assert "not connected" in event["summary"].lower() or "Drive" in event["summary"]
    assert event["payload"]["detail"] == "Drive is not connected."
    assert event["summary"] != "Drive is not connected."


def test_web_search_and_fetch_have_stable_shape():
    search = event_from_tool(
        "web_search",
        {"query": "Analytic Engines Ada"},
        {
            "results": [
                {"title": "Ada Lovelace", "url": "https://example.com/ada"},
                {"title": "Analytic", "url": "https://example.com/analytic"},
            ]
        },
        ok=True,
    )
    assert search is not None
    assert search["source"] == "web"
    assert search["kind"] == "search"
    assert search["tool"] == "web_search"
    assert search["payload"]["query"] == "Analytic Engines Ada"
    assert search["payload"]["count"] == 2
    assert search["payload"]["urls"] == [
        "https://example.com/ada",
        "https://example.com/analytic",
    ]
    fetch = event_from_tool(
        "web_fetch",
        {"url": "https://example.com/ada"},
        {"url": "https://example.com/ada", "title": "Ada Lovelace", "text": "long page"},
        ok=True,
    )
    assert fetch is not None
    assert fetch["source"] == "web"
    assert fetch["kind"] == "fetch"
    assert fetch["payload"]["url"] == "https://example.com/ada"
    assert fetch["payload"]["title"] == "Ada Lovelace"
    assert "text" not in fetch["payload"]


def test_payloads_do_not_include_tokens():
    event = event_from_tool(
        "drive_read",
        {"file_id": "d1"},
        {
            "id": "d1",
            "name": "Dinner notes",
            "access_token": "ya29.secret",
            "refresh_token": "1//secret",
            "Authorization": "Bearer ya29.secret",
        },
        ok=True,
    )
    assert event is not None
    blob = str(event["payload"])
    assert "ya29" not in blob
    assert "1//secret" not in blob
    assert "Bearer" not in blob
    assert "access_token" not in event["payload"]
    assert "refresh_token" not in event["payload"]
    assert "Authorization" not in event["payload"]


def test_mapper_output_is_legal_for_person_timeline(tmp_path):
    from coworker.people import PersonStore

    event = event_from_tool(
        "gmail_search",
        {"query": "from:ada"},
        {"messages": [{"id": "m1"}]},
        ok=True,
    )
    assert event is not None
    store = PersonStore(tmp_path)
    person = store.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    store.append_event(person["person_id"], actor="assistant", **event)
    timeline = store.timeline(person["person_id"])
    assert timeline[0]["source"] == "gmail"
    assert timeline[0]["kind"] == "mail"
    assert timeline[0]["payload"]["ids"] == ["m1"]
