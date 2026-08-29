import json

import pytest
from fastapi.testclient import TestClient

from coworker.permissions import decide
from coworker.people import PersonStore
from coworker.server import TOKEN_HEADER, create_app
from coworker.tools import OPENAI_TOOLS, execute

TOKEN = "test-token-person-board"


def _ada(store: PersonStore) -> dict:
    return store.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L***e",
        title="Founder",
        company="Analytic",
        target="research dinner",
    )


def test_board_tools_are_person_file_operations_not_a_second_crm():
    names = {schema["function"]["name"] for schema in OPENAI_TOOLS}
    assert {"board_get", "board_query", "board_upsert", "board_mutate", "board_delete"} <= names
    for name in ("board_get", "board_query", "board_upsert", "board_mutate"):
        decision = decide(name)
        assert decision.allowed is True
        assert decision.needs_user is False
    delete = decide("board_delete")
    assert delete.allowed is False
    assert delete.needs_user is True
    upsert = next(schema for schema in OPENAI_TOOLS if schema["function"]["name"] == "board_upsert")
    assert set(upsert["function"]["parameters"]["properties"]["record_type"]["enum"]) == {
        "artifact",
        "knowledge_gap",
        "source_ref",
    }
    board_get = next(
        schema for schema in OPENAI_TOOLS if schema["function"]["name"] == "board_get"
    )
    assert "person_id" not in board_get["function"]["parameters"].get("required", [])


def test_bound_board_get_returns_the_complete_living_brief_and_handoff(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    opened = store.set_sequence(ada["person_id"], "open", actor="director")
    store.bind_session("sess-ada", ada["person_id"])
    versions_before = store.versions(ada["person_id"])
    events_before = store.timeline(ada["person_id"])

    ok, result = execute(
        "board_get",
        {},
        people=store,
        session_id="sess-ada",
        run_id="run-brief",
    )

    assert ok is True
    assert result["status"] == "complete"
    assert result["partial"] is False
    assert result["person_id"] == ada["person_id"]
    assert result["person"]["version"] == opened["version"]
    assert "attachments" not in result["person"]
    assert result["brief"]["state"]["sequence"] == "open"
    assert set(result["brief"]["handoff"]) >= {
        "who",
        "wanted",
        "happened",
        "they_want",
        "generated",
    }
    assert result["brief"]["handoff"]["generated"] is True
    assert store.versions(ada["person_id"]) == versions_before
    assert store.timeline(ada["person_id"]) == events_before


def test_bound_board_get_refuses_a_different_person(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    other = store.keep_from_apollo(
        apollo_id="other",
        first_name="Grace",
        last_name_obfuscated="Hopper",
        title="Admiral",
        company="US Navy",
    )
    store.bind_session("sess-ada", ada["person_id"])

    ok, result = execute(
        "board_get",
        {"person_id": other["person_id"]},
        people=store,
        session_id="sess-ada",
    )

    assert ok is False
    assert result["status"] == "partial"
    assert result["code"] == "bound_person_mismatch"
    assert result["partial_sources"] == ["board"]


def test_board_get_bounds_large_stored_handoffs_without_changing_the_file(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    large = "x" * 100_000
    saved = store.patch(
        ada["person_id"],
        fields={
            "handoff_who": large,
            "handoff_wanted": large,
            "handoff_happened": large,
            "handoff_they_want": large,
        },
        expected_version=ada["version"],
        actor="director",
        rationale_summary="Store the reviewed handoff.",
    )
    store.bind_session("sess-ada", ada["person_id"])

    ok, result = execute("board_get", {}, people=store, session_id="sess-ada")

    assert ok is True
    assert len(json.dumps(result)) < 50_000
    assert not {
        "handoff_who",
        "handoff_wanted",
        "handoff_happened",
        "handoff_they_want",
    } & result["person"].keys()
    handoff = result["brief"]["handoff"]
    assert handoff["truncated_fields"] == [
        "who",
        "wanted",
        "happened",
        "they_want",
    ]
    assert all(len(handoff[field]) <= 2_001 for field in handoff["truncated_fields"])
    assert store.get(ada["person_id"])["handoff_who"] == large
    assert store.get(ada["person_id"])["version"] == saved["version"]


def test_board_get_failure_is_structured_and_names_the_unavailable_source(
    tmp_path, monkeypatch
):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.bind_session("sess-ada", ada["person_id"])

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("private sqlite detail")

    monkeypatch.setattr(store, "get", unavailable)
    ok, result = execute("board_get", {}, people=store, session_id="sess-ada")

    assert ok is False
    assert result == {
        "status": "partial",
        "partial": True,
        "code": "board_read_failed",
        "error": "The Board person-file read is unavailable.",
        "partial_sources": ["board"],
        "unavailable_sources": [{"source": "board", "code": "board_read_failed"}],
    }
    assert "sqlite" not in str(result)


def test_board_projection_failure_has_a_distinct_safe_ledger_code(tmp_path, monkeypatch):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.bind_session("sess-ada", ada["person_id"])

    def invalid_projection(*_args, **_kwargs):
        raise ValueError("foreign private source detail")

    monkeypatch.setattr("coworker.board_tools.project", invalid_projection)
    ok, result = execute("board_get", {}, people=store, session_id="sess-ada")

    assert ok is False
    assert result["code"] == "board_projection_failed"
    assert result["error"] == "The Board person file could not be projected safely."
    assert "private" not in str(result)


def test_board_upsert_does_not_create_a_person_or_fill_open(tmp_path):
    store = PersonStore(tmp_path)
    ok, result = execute(
        "board_upsert",
        {
            "person_id": "per_" + "0" * 32,
            "record_type": "artifact",
            "fields": {"title": "Draft"},
            "idempotency_key": "artifact:draft",
            "rationale_summary": "File a draft.",
        },
        people=store,
    )
    assert ok is False
    assert store.list_board() == {"open": [], "in_conversation": [], "done": []}


def test_keeping_then_patching_puts_ada_on_the_person_board(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.set_sequence(ada["person_id"], "open", actor="director")

    ok, patched = execute(
        "board_mutate",
        {
            "action": "patch",
            "person_id": ada["person_id"],
            "expected_version": store.get(ada["person_id"])["version"],
            "fields": {"title": "Founding partner"},
            "rationale_summary": "Correct the title from a cited source.",
        },
        people=store,
        actor="assistant",
        session_id="session-1",
        run_id="run-1",
    )

    assert ok is True
    assert patched["board_changed"] is True
    assert patched["person"]["title"] == "Founding partner"
    assert [row["person_id"] for row in store.list_board()["open"]] == [ada["person_id"]]
    receipt = store.timeline(ada["person_id"])[-1]
    assert receipt["actor"] == "assistant"
    assert receipt["session_id"] == "session-1"
    assert receipt["run_id"] == "run-1"


def test_stale_patch_cannot_overwrite_a_newer_human_edit(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.patch(
        ada["person_id"],
        fields={"title": "Director edit"},
        expected_version=1,
        actor="director",
        rationale_summary="Human corrected the title.",
    )
    ok, result = execute(
        "board_mutate",
        {
            "action": "patch",
            "person_id": ada["person_id"],
            "expected_version": 1,
            "fields": {"title": "Stale agent overwrite"},
            "rationale_summary": "This should not land.",
        },
        people=store,
    )
    assert ok is False
    assert "stale" in result["error"]
    assert store.get(ada["person_id"])["title"] == "Director edit"


def test_draft_artifact_cannot_move_a_person_to_in_conversation(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.set_sequence(ada["person_id"], "open", actor="director")
    execute(
        "board_upsert",
        {
            "person_id": ada["person_id"],
            "record_type": "artifact",
            "fields": {"title": "Outreach draft", "artifact_type": "gmail_draft"},
            "idempotency_key": "artifact:draft",
            "rationale_summary": "File the unsent draft.",
        },
        people=store,
    )
    version = store.get(ada["person_id"])["version"]
    ok, result = execute(
        "board_mutate",
        {
            "action": "transition",
            "person_id": ada["person_id"],
            "expected_version": version,
            "to_state": "in_conversation",
            "rationale_summary": "A draft is not a sent email.",
        },
        people=store,
        actor="assistant",
    )
    assert ok is False
    assert "in_conversation" in result["error"]
    assert store.get(ada["person_id"])["sequence_state"] == "open"


def test_sent_mail_lets_the_assistant_move_to_in_conversation(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.set_sequence(ada["person_id"], "open", actor="director")
    store.append_event(
        ada["person_id"],
        source="gmail",
        kind="send",
        summary="Sent draft d1",
        payload={"sent": True, "draft_id": "d1"},
        actor="assistant",
        tool="gmail_send",
    )
    version = store.get(ada["person_id"])["version"]
    ok, result = execute(
        "board_mutate",
        {
            "action": "transition",
            "person_id": ada["person_id"],
            "expected_version": version,
            "to_state": "in_conversation",
            "rationale_summary": "The sent mail opened the conversation.",
        },
        people=store,
        actor="assistant",
    )
    assert ok is True
    assert result["person"]["sequence_state"] == "in_conversation"


def test_reading_inbound_mail_lets_the_assistant_move_to_in_conversation(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.set_sequence(ada["person_id"], "open", actor="director")
    store.append_event(
        ada["person_id"],
        source="gmail",
        kind="mail",
        summary="Read mail Re: research dinner",
        payload={
            "id": "m1",
            "from": "ada@analytic.example",
            "subject": "Re: research dinner",
        },
        actor="assistant",
        tool="gmail_read",
    )
    ok, result = execute(
        "board_mutate",
        {
            "action": "transition",
            "person_id": ada["person_id"],
            "expected_version": store.get(ada["person_id"])["version"],
            "to_state": "in_conversation",
            "rationale_summary": "Ada replied.",
        },
        people=store,
        actor="assistant",
    )
    assert ok is True
    assert result["person"]["sequence_state"] == "in_conversation"


def test_gmail_search_mentioning_reply_cannot_move_to_in_conversation(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.set_sequence(ada["person_id"], "open", actor="director")
    store.append_event(
        ada["person_id"],
        source="gmail",
        kind="mail",
        summary="Searched Gmail for 'Ada reply' (0)",
        payload={"query": "Ada reply", "ids": []},
        actor="assistant",
        tool="gmail_search",
    )
    ok, result = execute(
        "board_mutate",
        {
            "action": "transition",
            "person_id": ada["person_id"],
            "expected_version": store.get(ada["person_id"])["version"],
            "to_state": "in_conversation",
            "rationale_summary": "A search is not a reply.",
        },
        people=store,
        actor="assistant",
    )
    assert ok is False
    assert "in_conversation" in result["error"]
    assert store.get(ada["person_id"])["sequence_state"] == "open"


def test_failed_gmail_send_cannot_move_to_in_conversation(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.set_sequence(ada["person_id"], "open", actor="director")
    store.append_event(
        ada["person_id"],
        source="gmail",
        kind="error",
        summary="Gmail send failed",
        payload={"detail": "gmail 500"},
        actor="assistant",
        tool="gmail_send",
    )
    ok, result = execute(
        "board_mutate",
        {
            "action": "transition",
            "person_id": ada["person_id"],
            "expected_version": store.get(ada["person_id"])["version"],
            "to_state": "in_conversation",
            "rationale_summary": "A failed send is not outreach.",
        },
        people=store,
        actor="assistant",
    )
    assert ok is False
    assert store.get(ada["person_id"])["sequence_state"] == "open"


def test_director_allow_can_move_without_sent_mail(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    updated = store.set_sequence(ada["person_id"], "in_conversation", actor="director")
    assert updated["sequence_state"] == "in_conversation"


def test_done_requires_an_outcome_for_the_assistant(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.set_sequence(ada["person_id"], "open", actor="director")
    version = store.get(ada["person_id"])["version"]
    ok, result = execute(
        "board_mutate",
        {
            "action": "transition",
            "person_id": ada["person_id"],
            "expected_version": version,
            "to_state": "done",
            "rationale_summary": "Close without an outcome.",
        },
        people=store,
        actor="assistant",
    )
    assert ok is False
    store.capture_outcome(
        ada["person_id"],
        outcome="not a fit",
        expected_version=store.get(ada["person_id"])["version"],
        actor="assistant",
        rationale_summary="Record the outcome first.",
    )
    ok, result = execute(
        "board_mutate",
        {
            "action": "transition",
            "person_id": ada["person_id"],
            "expected_version": store.get(ada["person_id"])["version"],
            "to_state": "done",
            "rationale_summary": "Close after the outcome.",
        },
        people=store,
        actor="assistant",
    )
    assert ok is True
    assert result["person"]["sequence_state"] == "done"
    assert result["person"]["outcome"] == "not a fit"


def test_restricted_source_is_hidden_on_get_query_and_patch_results(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    created = store.upsert_attachment(
        ada["person_id"],
        record_type="source_ref",
        fields={"title": "Resume", "excerpt": "SSN 123-45-6789", "sensitivity": "restricted"},
        idempotency_key="source:resume",
        actor="assistant",
        rationale_summary="File a restricted resume.",
    )
    assert created["restricted"] is True
    assert "excerpt" not in created
    expanded = store.get(ada["person_id"], expand_sources=True)
    assert expanded is not None
    assert expanded["sources"] == []
    assert expanded["restricted_source_count"] == 1
    granted = store.get(
        ada["person_id"],
        expand_sources=True,
        allowed_source_ids={created["id"]},
    )
    assert granted is not None
    assert granted["sources"][0]["fields"]["excerpt"] == "SSN 123-45-6789"


def test_conflicting_source_facts_do_not_silently_overwrite(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.upsert_attachment(
        ada["person_id"],
        record_type="source_ref",
        fields={"title": "Apollo", "claimed_title": "Founder"},
        idempotency_key="source:apollo-title",
        actor="assistant",
        rationale_summary="Apollo title.",
    )
    with pytest.raises(ValueError, match="idempotency conflict"):
        store.upsert_attachment(
            ada["person_id"],
            record_type="source_ref",
            fields={"title": "Apollo", "claimed_title": "Partner"},
            idempotency_key="source:apollo-title",
            actor="assistant",
            rationale_summary="Conflicting title.",
        )
    second = store.upsert_attachment(
        ada["person_id"],
        record_type="knowledge_gap",
        fields={"question": "Is the title Founder or Partner?"},
        idempotency_key="gap:title",
        actor="assistant",
        rationale_summary="Name the conflict.",
    )
    expanded = store.get(ada["person_id"], expand_sources=True)
    assert expanded is not None
    assert len(expanded["sources"]) == 1
    assert expanded["knowledge_gaps"][0]["id"] == second["id"]


def test_duplicate_apollo_keep_stays_one_person_on_the_board(tmp_path):
    store = PersonStore(tmp_path)
    first = store.keep_from_apollo(
        apollo_id="abc123",
        first_name="Alyssa",
        last_name_obfuscated="W***n",
        title="Partner",
        company="Codeology",
    )
    store.set_sequence(first["person_id"], "open", actor="director")
    second = store.keep_from_apollo(
        apollo_id="abc123",
        first_name="Alyssa",
        last_name_obfuscated="Wilson",
        title="Partner",
        company="Codeology",
    )
    assert second["person_id"] == first["person_id"]
    assert [row["person_id"] for row in store.list_board()["open"]] == [first["person_id"]]


def test_revert_restores_a_prior_person_version(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    patched = store.patch(
        ada["person_id"],
        fields={"title": "Wrong title"},
        expected_version=1,
        actor="assistant",
        rationale_summary="Bad source.",
    )
    reverted = store.revert(
        ada["person_id"],
        to_version=1,
        expected_version=patched["version"],
        actor="director",
        rationale_summary="Restore the verified title.",
    )
    assert reverted["title"] == "Founder"
    assert reverted["version"] == patched["version"] + 1
    assert PersonStore(tmp_path).get(ada["person_id"])["title"] == "Founder"


def test_delete_hides_the_person_from_the_board_and_keeps_a_receipt(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.set_sequence(ada["person_id"], "open", actor="director")
    deleted = store.delete(
        ada["person_id"],
        expected_version=store.get(ada["person_id"])["version"],
        actor="director",
        rationale_summary="Remove the duplicate after Allow.",
    )
    assert deleted["deleted"] is True
    assert store.get(ada["person_id"]) is None
    assert store.list_board() == {"open": [], "in_conversation": [], "done": []}
    assert store.timeline(ada["person_id"])[-1]["kind"] == "delete"


def test_keep_after_delete_restores_the_same_apollo_person(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.set_sequence(ada["person_id"], "open", actor="director")
    store.delete(
        ada["person_id"],
        expected_version=store.get(ada["person_id"])["version"],
        actor="director",
        rationale_summary="Remove the duplicate after Allow.",
    )
    ok, kept = execute(
        "people_keep",
        {
            "people": [
                {
                    "apolloId": "ada",
                    "firstName": "Ada",
                    "lastNameObfuscated": "L***e",
                    "title": "Founder",
                    "organizationName": "Analytic",
                }
            ],
            "target": "research dinner",
        },
        people=store,
    )
    assert ok is True
    person_id = kept["kept"][0]["person_id"]
    assert person_id == ada["person_id"]
    restored = store.get(person_id)
    assert restored is not None
    assert restored["deleted_at"] in (None, "")
    assert [row["person_id"] for row in store.list_board()["open"]] == [person_id]
    ok, opened = execute(
        "board_mutate",
        {
            "action": "transition",
            "person_id": person_id,
            "expected_version": restored["version"],
            "to_state": "open",
            "rationale_summary": "File Ada again.",
        },
        people=store,
    )
    assert ok is True
    assert opened["person"]["sequence_state"] == "open"


def test_query_filters_board_people_by_sequence_and_company(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.set_sequence(ada["person_id"], "open", actor="director")
    rows = store.query(sequence="open", company="Analytic", target="research dinner")
    assert [row["person_id"] for row in rows] == [ada["person_id"]]
    assert store.query(sequence="done") == []


def test_keep_then_open_lands_on_the_person_board(tmp_path):
    store = PersonStore(tmp_path)
    ok, kept = execute(
        "people_keep",
        {
            "people": [
                {
                    "apolloId": "ada",
                    "firstName": "Ada",
                    "lastNameObfuscated": "L***e",
                    "title": "Founder",
                    "organizationName": "Analytic",
                }
            ],
            "target": "research dinner",
        },
        people=store,
    )
    assert ok is True
    person_id = kept["kept"][0]["person_id"]
    assert store.list_board()["open"] == []
    ok, opened = execute(
        "board_mutate",
        {
            "action": "transition",
            "person_id": person_id,
            "expected_version": store.get(person_id)["version"],
            "to_state": "open",
            "rationale_summary": "Start the sequence.",
        },
        people=store,
    )
    assert ok is True
    assert [row["person_id"] for row in store.list_board()["open"]] == [person_id]


def test_approved_board_delete_uses_the_human_actor(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    app.state.people.set_sequence(person["person_id"], "open", actor="director")
    item = app.state.inbox.park(
        "board_delete",
        {
            "person_id": person["person_id"],
            "expected_version": app.state.people.get(person["person_id"])["version"],
            "rationale_summary": "Delete the duplicate after review.",
        },
        item_id="board-delete-approved",
        session_id="session-delete",
        run_id="run-delete",
    )
    response = TestClient(app).post(
        f"/v1/inbox/{item['id']}",
        headers={TOKEN_HEADER: TOKEN},
        json={"decision": "allow", "actor": "director", "scope": "once"},
    )
    assert response.status_code == 200
    assert app.state.people.get(person["person_id"]) is None
    assert app.state.people.list_board()["open"] == []
    assert app.state.people.timeline(person["person_id"])[-1]["actor"] == "director"


def test_person_api_lists_versions_and_reverts(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path)
    person = app.state.people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    updated = app.state.people.patch(
        person["person_id"],
        fields={"title": "Wrong"},
        expected_version=1,
        actor="assistant",
        rationale_summary="Bad title.",
    )
    client = TestClient(app)
    detail = client.get(
        f"/v1/people/{person['person_id']}", headers={TOKEN_HEADER: TOKEN}
    )
    assert detail.status_code == 200
    assert detail.json()["person"]["title"] == "Wrong"
    assert [row["version"] for row in detail.json()["versions"]]
    reverted = client.post(
        f"/v1/people/{person['person_id']}/revert",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "to_version": 1,
            "expected_version": updated["version"],
            "rationale_summary": "Restore Founder.",
        },
    )
    assert reverted.status_code == 200
    assert reverted.json()["person"]["title"] == "Founder"
