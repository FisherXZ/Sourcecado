import pytest

from coworker.people import PersonStore, VersionConflict


def _person(store: PersonStore, **overrides):
    defaults = dict(
        apollo_id="apollo-1",
        first_name="Maya",
        last_name_obfuscated="Chen",
        title="Software Engineer",
        company="Stripe",
    )
    defaults.update(overrides)
    return store.keep_from_apollo(**defaults)


def test_task_shows_the_same_id_in_global_and_person_lists(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    dated = store.create_task(
        person["person_id"],
        title="Send project examples",
        due_date="2026-10-09",
        actor="director",
    )
    undated = store.create_task(
        person["person_id"], title="Send portfolio link", actor="director"
    )

    global_ids = {task["task_id"] for task in store.list_tasks()}
    person_ids = {task["task_id"] for task in store.list_tasks(person_id=person["person_id"])}
    assert dated["task_id"] in global_ids
    assert undated["task_id"] in global_ids
    assert global_ids == person_ids  # one person, same records in both views


def test_tasks_survive_restart_without_gmail_or_model(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    task = store.create_task(
        person["person_id"], title="Draft proposal", actor="director"
    )

    reopened = PersonStore(tmp_path)
    loaded = reopened.list_tasks(person_id=person["person_id"])
    assert [t["task_id"] for t in loaded] == [task["task_id"]]
    assert loaded[0]["title"] == "Draft proposal"


def test_repeated_create_with_same_operation_id_does_not_duplicate(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    first = store.create_task(
        person["person_id"],
        title="Reply to question",
        actor="director",
        operation_id="op-123",
    )
    replay = store.create_task(
        person["person_id"],
        title="Reply to question",
        actor="director",
        operation_id="op-123",
    )
    assert replay["task_id"] == first["task_id"]
    assert len(store.list_tasks(person_id=person["person_id"])) == 1


def test_stale_update_raises_conflict_and_leaves_the_task_unchanged(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    task = store.create_task(
        person["person_id"], title="Original title", actor="director"
    )
    # Someone else already bumped the version to 2.
    store.update_task(
        task["task_id"], expected_version=1, actor="director", title="Edited once"
    )

    with pytest.raises(VersionConflict):
        store.update_task(
            task["task_id"],
            expected_version=1,  # stale
            actor="director",
            title="Conflicting edit",
        )

    current = store.get_task(task["task_id"])
    assert current["title"] == "Edited once"  # the stale write did not land
    assert current["version"] == 2


def test_completing_a_task_sets_state_and_timestamp(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    task = store.create_task(person["person_id"], title="Send note", actor="director")

    completed = store.update_task(
        task["task_id"], expected_version=1, actor="director", state="completed"
    )
    assert completed["state"] == "completed"
    assert completed["completed_at"] is not None
    assert store.list_tasks(person_id=person["person_id"], state="open") == []

    reopened = store.update_task(
        task["task_id"],
        expected_version=completed["version"],
        actor="director",
        state="open",
    )
    assert reopened["state"] == "open"
    assert reopened["completed_at"] is None


def test_task_rejects_unknown_person_and_member(tmp_path):
    store = PersonStore(tmp_path)
    with pytest.raises(ValueError):
        store.create_task("per_missing", title="x", actor="director")

    person = _person(store)
    with pytest.raises(ValueError):
        store.create_task(
            person["person_id"],
            title="x",
            actor="director",
            assignee_member_id="mem_missing",
        )


def test_due_date_must_be_an_explicit_calendar_date(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    with pytest.raises(ValueError):
        store.create_task(
            person["person_id"], title="x", actor="director", due_date="next friday"
        )


def test_members_and_owner_round_trip_onto_the_board(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    member = store.add_member("Milan")
    assert store.add_member("Milan")["member_id"] == member["member_id"]  # idempotent

    store.patch(
        person["person_id"],
        fields={"owner_member_id": member["member_id"]},
        expected_version=int(person["version"]),
        actor="director",
        rationale_summary="Set owner",
    )
    board = store.list_board()
    rows = [p for lane in board.values() for p in lane]
    owned = next(p for p in rows if p["person_id"] == person["person_id"])
    assert owned["owner_member_id"] == member["member_id"]
    assert owned["owner_name"] == "Milan"


def test_assignee_name_resolves_in_task_rows(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    member = store.add_member("Priya")
    task = store.create_task(
        person["person_id"],
        title="Share deck",
        actor="director",
        assignee_member_id=member["member_id"],
    )
    assert task["assignee_name"] == "Priya"
    assert store.list_tasks(assignee_member_id=member["member_id"])[0]["task_id"] == (
        task["task_id"]
    )
