import pytest

from coworker.people import PersonStore


def test_keeping_apollo_row_files_person_without_email(tmp_path):
    store = PersonStore(tmp_path)
    person = store.keep_from_apollo(
        apollo_id="abc123",
        first_name="Alyssa",
        last_name_obfuscated="W***n",
        title="Partner",
        company="Codeology",
        target="club research dinner",
    )
    loaded = store.get(person["person_id"])
    assert loaded is not None
    assert loaded["apollo_id"] == "abc123"
    assert loaded["first_name"] == "Alyssa"
    assert loaded["last_name"] == "W***n"
    assert loaded["title"] == "Partner"
    assert loaded["company"] == "Codeology"
    assert loaded["target"] == "club research dinner"
    assert loaded["email"] is None
    assert loaded["sequence_state"] is None
    restarted = PersonStore(tmp_path)
    assert restarted.get(person["person_id"])["email"] is None
    assert restarted.get(person["person_id"])["first_name"] == "Alyssa"


def test_keeping_same_apollo_id_updates_instead_of_forking(tmp_path):
    store = PersonStore(tmp_path)
    first = store.keep_from_apollo(
        apollo_id="abc123",
        first_name="Alyssa",
        last_name_obfuscated="W***n",
        title="Partner",
        company="Codeology",
    )
    second = store.keep_from_apollo(
        apollo_id="abc123",
        first_name="Alyssa",
        last_name_obfuscated="Wilson",
        title="General Partner",
        company="Codeology",
        target="follow up",
    )
    assert second["person_id"] == first["person_id"]
    loaded = PersonStore(tmp_path).get_by_apollo_id("abc123")
    assert loaded is not None
    assert loaded["person_id"] == first["person_id"]
    assert loaded["title"] == "General Partner"
    assert loaded["target"] == "follow up"
    assert loaded["last_name"] == "Wilson"
    assert loaded["email"] is None
    assert store.get(first["person_id"])["title"] == "General Partner"


def _ada(store: PersonStore) -> dict:
    return store.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L***e",
        title="Founder",
        company="Analytic",
    )


def test_person_with_no_sequence_is_not_on_the_board(tmp_path):
    store = PersonStore(tmp_path)
    _ada(store)
    assert store.list_board() == {"open": [], "in_conversation": [], "done": []}


def test_moving_person_to_open_puts_them_on_the_board(tmp_path):
    store = PersonStore(tmp_path)
    person = _ada(store)
    updated = store.set_sequence(person["person_id"], "open", actor="director")
    assert updated["sequence_state"] == "open"
    board = store.list_board()
    assert [row["person_id"] for row in board["open"]] == [person["person_id"]]
    assert board["in_conversation"] == []
    assert board["done"] == []
    restarted = PersonStore(tmp_path)
    assert [row["person_id"] for row in restarted.list_board()["open"]] == [
        person["person_id"]
    ]


def test_unknown_sequence_actor_or_person_is_rejected(tmp_path):
    store = PersonStore(tmp_path)
    person = _ada(store)
    with pytest.raises(ValueError, match="sequence"):
        store.set_sequence(person["person_id"], "won", actor="director")
    with pytest.raises(ValueError, match="actor"):
        store.set_sequence(person["person_id"], "open", actor="crm")
    with pytest.raises(ValueError, match="unknown person"):
        store.set_sequence("per_" + "0" * 32, "open", actor="assistant")
    assert store.list_board() == {"open": [], "in_conversation": [], "done": []}
    assert store.get(person["person_id"])["sequence_state"] is None


def _alonzo(store: PersonStore) -> dict:
    return store.keep_from_apollo(
        apollo_id="alonzo",
        first_name="Alonzo",
        last_name_obfuscated="C",
        title="Professor",
        company="Princeton",
    )


def test_gmail_event_on_ada_does_not_show_up_on_alonzo(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    alonzo = _alonzo(store)
    first = store.append_event(
        ada["person_id"],
        source="gmail",
        kind="mail",
        summary="Read mail from Ada",
        payload={"message_id": "m1"},
        actor="assistant",
        tool="gmail_read",
        session_id="sess1",
    )
    store.append_event(
        ada["person_id"],
        source="drive",
        kind="file",
        summary="Read deck",
        payload={"file_id": "d1"},
        actor="assistant",
        tool="drive_read",
    )
    store.append_event(
        alonzo["person_id"],
        source="granola",
        kind="meeting",
        summary="Meeting notes",
        payload={"title": "catch up"},
        actor="assistant",
        tool="mcp__granola__get_note",
    )
    timeline = store.timeline(ada["person_id"])
    assert [row["kind"] for row in timeline] == ["mail", "file"]
    assert timeline[0]["event_id"] == first["event_id"]
    assert timeline[0]["source"] == "gmail"
    assert timeline[0]["payload"] == {"message_id": "m1"}
    assert timeline[0]["session_id"] == "sess1"
    assert all(row["person_id"] == ada["person_id"] for row in timeline)
    alonzo_timeline = PersonStore(tmp_path).timeline(alonzo["person_id"])
    assert [row["kind"] for row in alonzo_timeline] == ["meeting"]


def test_hubspot_is_not_a_source(tmp_path):
    store = PersonStore(tmp_path)
    person = _ada(store)
    with pytest.raises(ValueError, match="source"):
        store.append_event(
            person["person_id"],
            source="hubspot",
            kind="deal",
            summary="nope",
            actor="assistant",
        )
    assert store.timeline(person["person_id"]) == []


def test_session_can_be_rebound_to_a_different_person(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    alonzo = _alonzo(store)
    store.bind_session("chat-1", ada["person_id"])
    assert store.person_for_session("chat-1") == ada["person_id"]
    store.bind_session("chat-1", alonzo["person_id"])
    assert store.person_for_session("chat-1") == alonzo["person_id"]
    assert store.person_for_session("chat-2") is None
    with pytest.raises(ValueError, match="unknown person"):
        store.bind_session("chat-1", "per_" + "0" * 32)
    restarted = PersonStore(tmp_path)
    assert restarted.person_for_session("chat-1") == alonzo["person_id"]


def test_handoff_four_fields_survive_get(tmp_path):
    store = PersonStore(tmp_path)
    person = _ada(store)
    updated = store.set_handoff(
        person["person_id"],
        who="Ada, founder at Analytic",
        wanted="Invite her to the research dinner",
        happened="Drafted, not sent",
        they_want="She asked about student builders",
    )
    assert updated["handoff_who"] == "Ada, founder at Analytic"
    assert updated["handoff_wanted"] == "Invite her to the research dinner"
    assert updated["handoff_happened"] == "Drafted, not sent"
    assert updated["handoff_they_want"] == "She asked about student builders"
    loaded = PersonStore(tmp_path).get(person["person_id"])
    assert loaded is not None
    assert loaded["handoff_they_want"] == "She asked about student builders"
