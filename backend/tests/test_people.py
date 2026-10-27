import pytest

from coworker.crm_tasks import TaskConflict
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
    assert loaded["last_name"] is None
    assert loaded["last_name_status"] == "hidden_by_apollo"
    assert "W***n" not in str(loaded)
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


def test_keeping_a_thinner_apollo_row_does_not_blank_the_person_file(tmp_path):
    store = PersonStore(tmp_path)
    kept = store.keep_from_apollo(
        apollo_id="abc123",
        first_name="Alyssa",
        last_name_obfuscated="W***n",
        title="Partner",
        company="Codeology",
        target="club research dinner",
    )
    again = store.keep_from_apollo(
        apollo_id="abc123",
        first_name="Alyssa",
        last_name_obfuscated=None,
        title=None,
        company=None,
    )
    assert again["person_id"] == kept["person_id"]
    assert again["last_name"] is None
    assert again["last_name_status"] == "hidden_by_apollo"
    assert again["title"] == "Partner"
    assert again["company"] == "Codeology"
    assert again["target"] == "club research dinner"


def test_blank_apollo_fields_count_as_missing_not_as_a_value(tmp_path):
    store = PersonStore(tmp_path)
    store.keep_from_apollo(
        apollo_id="abc123",
        first_name="Alyssa",
        last_name_obfuscated="W***n",
        title="Partner",
        company="Codeology",
    )
    again = store.keep_from_apollo(
        apollo_id="abc123",
        first_name="  ",
        last_name_obfuscated="",
        title="",
        company="   ",
    )
    assert again["first_name"] == "Alyssa"
    assert again["last_name"] is None
    assert again["last_name_status"] == "hidden_by_apollo"
    assert again["title"] == "Partner"
    assert again["company"] == "Codeology"


def test_person_patch_refuses_an_obfuscated_canonical_surname(tmp_path):
    store = PersonStore(tmp_path)
    person = store.keep_from_apollo(
        apollo_id="abc123",
        first_name="Alyssa",
        last_name_obfuscated="W***n",
        title="Partner",
        company="Codeology",
    )

    with pytest.raises(ValueError, match="obfuscated"):
        store.patch(
            person["person_id"],
            fields={"last_name": "W***n"},
            expected_version=person["version"],
            actor="director",
            rationale_summary="Do not store Apollo's mask as a name.",
        )

    loaded = store.get(person["person_id"])
    assert loaded is not None
    assert loaded["last_name"] is None
    assert loaded["last_name_status"] == "hidden_by_apollo"


def test_person_handoff_preserves_ordinary_markdown_emphasis(tmp_path):
    store = PersonStore(tmp_path)
    person = store.keep_from_apollo(
        apollo_id="abc123",
        first_name="Alyssa",
        last_name_obfuscated="W***n",
        title="Partner",
        company="Codeology",
    )
    prose = "This is **important**. Use *carefully*, please."

    updated = store.patch(
        person["person_id"],
        fields={"handoff_happened": prose},
        expected_version=person["version"],
        actor="director",
        rationale_summary="Preserve the director's formatting.",
    )

    assert updated["handoff_happened"] == prose
    assert PersonStore(tmp_path).get(person["person_id"])["handoff_happened"] == prose


def _ada(store: PersonStore) -> dict:
    return store.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L***e",
        title="Founder",
        company="Analytic",
    )


def test_person_with_no_sequence_is_on_the_board_backlog(tmp_path):
    store = PersonStore(tmp_path)
    person = _ada(store)

    board = store.list_board()

    assert person["sequence_state"] is None
    assert [row["person_id"] for row in board["backlog"]] == [person["person_id"]]
    assert board["backlog"][0]["board_lane"] == "backlog"
    assert board["open"] == []
    assert board["in_conversation"] == []
    assert board["done"] == []


def test_unknown_legacy_sequence_appears_in_backlog_without_outreach(tmp_path):
    store = PersonStore(tmp_path)
    person = _ada(store)
    store._conn.execute(
        "UPDATE people SET sequence_state = 'kept' WHERE person_id = ?",
        (person["person_id"],),
    )
    store._conn.commit()

    row = store.list_board()["backlog"][0]

    assert row["person_id"] == person["person_id"]
    assert row["sequence_state"] == "kept"
    assert row["board_lane"] == "backlog"
    assert row["last_contact_at"] is None
    assert row["replied"] is False
    assert row["follow_up"] == {"needed": False, "reason": None}


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
    assert [row["person_id"] for row in store.list_board()["backlog"]] == [
        person["person_id"]
    ]
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


def test_session_binding_is_idempotent_and_cannot_be_rebound(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    alonzo = _alonzo(store)
    store.bind_session("chat-1", ada["person_id"])
    assert store.person_for_session("chat-1") == ada["person_id"]
    store.bind_session("chat-1", ada["person_id"])
    with pytest.raises(ValueError, match="already bound"):
        store.bind_session("chat-1", alonzo["person_id"])
    assert store.person_for_session("chat-1") == ada["person_id"]
    assert store.person_for_session("chat-2") is None
    with pytest.raises(ValueError, match="unknown person"):
        store.bind_session("chat-1", "per_" + "0" * 32)
    restarted = PersonStore(tmp_path)
    assert restarted.person_for_session("chat-1") == ada["person_id"]


def test_session_binding_rejects_a_stale_person_version(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)

    with pytest.raises(ValueError, match="stale record version"):
        store.bind_session(
            "chat-1",
            ada["person_id"],
            expected_person_version=ada["version"] + 1,
        )

    assert store.person_for_session("chat-1") is None


def test_person_has_one_durable_bound_sourcing_session(tmp_path):
    store = PersonStore(tmp_path)
    ada = _ada(store)
    store.bind_session("chat-1", ada["person_id"])

    with pytest.raises(ValueError, match="already has a bound sourcing session"):
        store.bind_session("chat-2", ada["person_id"])

    assert store.session_for_person(ada["person_id"]) == "chat-1"
    restarted = PersonStore(tmp_path)
    assert restarted.session_for_person(ada["person_id"]) == "chat-1"


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


# --- status changes on the CRM change/undo path --------------------------

def _opened_ada(store: PersonStore) -> dict:
    """An Ada moved to Open, so a status change has somewhere to go."""
    _ada(store)
    person = store.list_board()["backlog"][0]
    return store.set_sequence(person["person_id"], "open", actor="director")


def _status_changes(store: PersonStore, person_id: str) -> list:
    rows = store._conn.execute(
        """
        SELECT action, before_json, after_json, compensates_change_id
        FROM crm_changes WHERE person_id = ? AND entity_type = 'status'
        ORDER BY created_at
        """,
        (person_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def test_change_status_records_history_and_updates_the_board(tmp_path):
    store = PersonStore(tmp_path)
    opened = _opened_ada(store)
    pid = opened["person_id"]

    result = store.change_status(
        pid, "in_conversation", expected_version=opened["version"], operation_id="op-change-01"
    )

    assert result["replayed"] is False
    assert result["person"]["sequence_state"] == "in_conversation"
    assert result["person"]["version"] == opened["version"] + 1
    assert result["change"]["entity_type"] == "status"
    assert result["change"]["action"] == "update"
    recorded = _status_changes(store, pid)
    assert len(recorded) == 1
    assert '"sequence_state": "open"' in recorded[0]["before_json"]
    assert '"sequence_state": "in_conversation"' in recorded[0]["after_json"]
    # The board reflects the move through its ordinary read.
    assert [row["person_id"] for row in store.list_board()["in_conversation"]] == [pid]


def test_change_status_to_the_current_status_is_a_no_op(tmp_path):
    store = PersonStore(tmp_path)
    opened = _opened_ada(store)
    pid = opened["person_id"]

    result = store.change_status(
        pid, "open", expected_version=opened["version"], operation_id="op-noop-001"
    )

    assert result["change"] is None
    assert result["person"]["version"] == opened["version"]
    assert _status_changes(store, pid) == []


def test_change_status_replays_the_same_operation_without_a_second_change(tmp_path):
    store = PersonStore(tmp_path)
    opened = _opened_ada(store)
    pid = opened["person_id"]

    first = store.change_status(
        pid, "in_conversation", expected_version=opened["version"], operation_id="op-replay-01"
    )
    replay = store.change_status(
        pid, "in_conversation", expected_version=opened["version"], operation_id="op-replay-01"
    )

    assert replay["replayed"] is True
    assert replay["change"]["change_id"] == first["change"]["change_id"]
    assert store.get(pid)["version"] == first["person"]["version"]
    assert len(_status_changes(store, pid)) == 1


def test_stale_expected_version_conflicts_with_the_current_person(tmp_path):
    store = PersonStore(tmp_path)
    opened = _opened_ada(store)
    pid = opened["person_id"]

    with pytest.raises(TaskConflict) as caught:
        store.change_status(
            pid, "in_conversation", expected_version=opened["version"] - 1, operation_id="op-stale-001"
        )

    assert caught.value.code == "status_conflict"
    assert caught.value.current["person"]["person_id"] == pid
    assert store.get(pid)["sequence_state"] == "open"


def test_undo_status_change_restores_the_prior_status(tmp_path):
    store = PersonStore(tmp_path)
    opened = _opened_ada(store)
    pid = opened["person_id"]
    moved = store.change_status(
        pid, "in_conversation", expected_version=opened["version"], operation_id="op-move-0001"
    )

    undone = store.undo_status_change(
        pid, moved["change"]["change_id"], operation_id="op-undo-0001"
    )

    assert undone["person"]["sequence_state"] == "open"
    assert undone["change"]["action"] == "undo"
    assert undone["change"]["compensates_change_id"] == moved["change"]["change_id"]
    assert store.get(pid)["sequence_state"] == "open"


def test_undo_leaves_tasks_and_descriptions_and_survives_unrelated_edits(tmp_path):
    from coworker.crm_tasks import TaskService

    store = PersonStore(tmp_path)
    opened = _opened_ada(store)
    pid = opened["person_id"]
    moved = store.change_status(
        pid, "in_conversation", expected_version=opened["version"], operation_id="op-move-0002"
    )
    # Unrelated work after the status change: a task and a human description
    # edit (the description edit bumps the person version).
    task = TaskService(store).create(
        pid,
        title="Send the proposal",
        timezone="America/Los_Angeles",
        operation_id="op-task-0001",
        actor="director",
    )["task"]
    store.patch_description(
        pid,
        "general",
        expected_version=0,
        text="Evaluating a Codeology collaboration.",
        actor="director",
        rationale_summary="offline note",
    )

    # The undo is not stale: status itself was not touched again.
    undone = store.undo_status_change(
        pid, moved["change"]["change_id"], operation_id="op-undo-0002"
    )

    assert undone["person"]["sequence_state"] == "open"
    assert TaskService(store).get(pid, task["task_id"])["state"] == "open"
    descriptions = store.descriptions(pid)
    assert descriptions["general"]["text"] == "Evaluating a Codeology collaboration."
    assert descriptions["general"]["authority"] == "human"


def test_undo_is_refused_after_a_later_status_edit(tmp_path):
    store = PersonStore(tmp_path)
    opened = _opened_ada(store)
    pid = opened["person_id"]
    first = store.change_status(
        pid, "in_conversation", expected_version=opened["version"], operation_id="op-aaaa-0001"
    )
    # A later status edit moves the person past the first change's result.
    store.change_status(
        pid, "done", expected_version=first["person"]["version"], operation_id="op-bbbb-0002"
    )

    with pytest.raises(TaskConflict) as caught:
        store.undo_status_change(pid, first["change"]["change_id"], operation_id="op-cccc-0003")

    assert caught.value.code == "status_conflict"
    assert caught.value.current["person"]["sequence_state"] == "done"
    # Nothing was reverted; the later status stands.
    assert store.get(pid)["sequence_state"] == "done"
