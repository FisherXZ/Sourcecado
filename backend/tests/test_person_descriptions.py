"""Person descriptions (ticket #202): per-slot text with human authority.

Covers the store behavior and the PATCH route: lazy rows, the assistant→human
flip on save, intentional protected blanks, release back to the assistant,
per-slot concurrency, char limits, restart persistence, separation from the
handoff and activity history, and snapshot coverage.
"""

import json

import pytest
from fastapi.testclient import TestClient

from coworker.people import PersonStore
from coworker.provider import FakeProvider
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-descriptions"


def _person(store: PersonStore) -> dict:
    return store.keep_from_apollo(
        apollo_id="desc-1",
        first_name="Alyssa",
        last_name_obfuscated="W***n",
        title="Partner",
        company="Codeology",
        target="club research dinner",
    )


def _save(store, person_id, slot, text, *, expected_version):
    return store.patch_description(
        person_id,
        slot,
        expected_version=expected_version,
        text=text,
        actor="director",
        rationale_summary="Director edited the description.",
    )


# --- store: defaults and the "Not summarized yet" distinction ---------------


def test_missing_slots_default_to_assistant_and_unsummarized(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    descriptions = store.descriptions(person["person_id"])
    assert set(descriptions) == {"general", "detailed"}
    for slot in ("general", "detailed"):
        assert descriptions[slot]["text"] is None
        assert descriptions[slot]["authority"] == "assistant"
        assert descriptions[slot]["version"] == 0


def test_saving_text_flips_authority_to_human_and_bumps_slot_version(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    saved = _save(
        store, person["person_id"], "general", "Warm intro from the dinner.",
        expected_version=0,
    )
    assert saved["authority"] == "human"
    assert saved["text"] == "Warm intro from the dinner."
    assert saved["version"] == 1


def test_intentional_blank_is_protected_not_unsummarized(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    saved = _save(store, person["person_id"], "general", "", expected_version=0)
    # An intentional human blank is a protected empty string, distinct from the
    # never-touched default (text None, authority assistant) that the UI reads
    # as "Not summarized yet".
    assert saved["text"] == ""
    assert saved["authority"] == "human"


def test_release_returns_slot_to_assistant_and_keeps_text(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    _save(store, person["person_id"], "detailed", "A long readable note.",
          expected_version=0)
    released = store.patch_description(
        person["person_id"],
        "detailed",
        expected_version=1,
        release=True,
        actor="director",
        rationale_summary="Director let the assistant maintain the description.",
    )
    assert released["authority"] == "assistant"
    # Release hands ownership back but leaves the text for a later writer.
    assert released["text"] == "A long readable note."
    assert released["version"] == 2


# --- store: concurrency, limits, independence -------------------------------


def test_stale_slot_version_is_rejected(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    _save(store, person["person_id"], "general", "First.", expected_version=0)
    with pytest.raises(ValueError, match="stale record version"):
        _save(store, person["person_id"], "general", "Second.", expected_version=0)


def test_over_limit_text_is_rejected(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    with pytest.raises(ValueError, match="exceeds 500"):
        _save(store, person["person_id"], "general", "x" * 501, expected_version=0)
    # The detailed slot has the larger ceiling.
    assert _save(
        store, person["person_id"], "detailed", "y" * 8000, expected_version=0
    )["version"] == 1


def test_slots_have_independent_versions(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    _save(store, person["person_id"], "general", "General.", expected_version=0)
    # The detailed slot is untouched, so it still saves from version 0.
    detailed = _save(
        store, person["person_id"], "detailed", "Detailed.", expected_version=0
    )
    assert detailed["version"] == 1
    assert store.descriptions(person["person_id"])["general"]["version"] == 1


# --- store: separation, persistence, snapshot -------------------------------


def test_descriptions_stay_out_of_handoff_and_activity(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    _save(store, person["person_id"], "general", "Secret summary.",
          expected_version=0)
    row = store._conn.execute(
        "SELECT * FROM people WHERE person_id = ?", (person["person_id"],)
    ).fetchone()
    for field in (
        "handoff_who", "handoff_wanted", "handoff_happened", "handoff_they_want"
    ):
        assert row[field] is None
    # The audit receipt records the slot/authority but never the text itself.
    events = store.timeline(person["person_id"])
    description_events = [e for e in events if e["kind"] == "description"]
    assert description_events
    assert "Secret summary." not in json.dumps(
        [e["payload"] for e in description_events]
    )


def test_text_and_authority_survive_restart(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    _save(store, person["person_id"], "general", "Durable.", expected_version=0)
    store.patch_description(
        person["person_id"],
        "detailed",
        expected_version=0,
        text="",
        actor="director",
        rationale_summary="Director cleared the detailed description.",
    )
    reopened = PersonStore(tmp_path).descriptions(person["person_id"])
    assert reopened["general"]["text"] == "Durable."
    assert reopened["general"]["authority"] == "human"
    # The protected blank survives too, still distinct from unsummarized.
    assert reopened["detailed"]["text"] == ""
    assert reopened["detailed"]["authority"] == "human"


def test_snapshot_captures_descriptions(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    _save(store, person["person_id"], "general", "Snapshot me.",
          expected_version=0)
    latest = store._conn.execute(
        """
        SELECT person_json FROM person_versions
        WHERE person_id = ? ORDER BY version DESC LIMIT 1
        """,
        (person["person_id"],),
    ).fetchone()
    captured = json.loads(latest["person_json"])["descriptions"]
    assert captured["general"]["text"] == "Snapshot me."
    assert captured["general"]["authority"] == "human"


# --- store: revert restores descriptions (spec §8.2) ------------------------


def _revert(store, person_id, *, to_version, expected_version):
    return store.revert(
        person_id,
        to_version=to_version,
        expected_version=expected_version,
        actor="director",
        rationale_summary="Director restored an earlier person version.",
    )


def test_revert_restores_replaced_description_text_and_protection(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    _save(store, pid, "general", "Original summary.", expected_version=0)
    original = store.get(pid)["version"]
    _save(store, pid, "general", "Replaced summary.", expected_version=1)
    replaced = store.get(pid)["version"]

    _revert(store, pid, to_version=original, expected_version=replaced)

    restored = store.descriptions(pid)["general"]
    assert restored["text"] == "Original summary."
    assert restored["authority"] == "human"
    assert restored["version"] == 1


def test_revert_restores_human_protection_after_release(tmp_path):
    # The reviewer's second check: releasing protection then reverting to the
    # protected snapshot must bring authority back to human, not leave it
    # assistant (spec §8.2 — a revert cannot silently un-protect a human edit).
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    _save(store, pid, "detailed", "Mine to keep.", expected_version=0)
    protected = store.get(pid)["version"]
    store.patch_description(
        pid,
        "detailed",
        expected_version=1,
        release=True,
        actor="director",
        rationale_summary="Director let the assistant maintain the description.",
    )
    released_at = store.get(pid)["version"]
    assert store.descriptions(pid)["detailed"]["authority"] == "assistant"

    _revert(store, pid, to_version=protected, expected_version=released_at)

    restored = store.descriptions(pid)["detailed"]
    assert restored["authority"] == "human"
    assert restored["text"] == "Mine to keep."


def test_revert_to_a_pre_description_version_clears_the_row(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    baseline = store.get(pid)["version"]  # snapshot has no description rows yet
    _save(store, pid, "general", "Written later.", expected_version=0)
    after_save = store.get(pid)["version"]

    _revert(store, pid, to_version=baseline, expected_version=after_save)

    general = store.descriptions(pid)["general"]
    assert general["text"] is None
    assert general["authority"] == "assistant"
    assert general["version"] == 0


# --- API --------------------------------------------------------------------


def _app(tmp_path):
    return create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())


def _headers():
    return {TOKEN_HEADER: TOKEN}


def test_patch_saves_and_get_returns_descriptions(tmp_path):
    app = _app(tmp_path)
    person = _person(app.state.people)
    client = TestClient(app)
    res = client.patch(
        f"/v1/people/{person['person_id']}/descriptions/general",
        headers=_headers(),
        json={"expected_version": 0, "text": "Concise overview."},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["saved"] is True
    assert body["descriptions"]["general"]["authority"] == "human"
    got = client.get(
        f"/v1/people/{person['person_id']}", headers=_headers()
    ).json()
    assert got["descriptions"]["general"]["text"] == "Concise overview."


def test_patch_stale_version_is_409(tmp_path):
    app = _app(tmp_path)
    person = _person(app.state.people)
    client = TestClient(app)
    client.patch(
        f"/v1/people/{person['person_id']}/descriptions/general",
        headers=_headers(),
        json={"expected_version": 0, "text": "First."},
    )
    res = client.patch(
        f"/v1/people/{person['person_id']}/descriptions/general",
        headers=_headers(),
        json={"expected_version": 0, "text": "Clobber."},
    )
    assert res.status_code == 409


def test_patch_over_limit_is_422(tmp_path):
    app = _app(tmp_path)
    person = _person(app.state.people)
    res = TestClient(app).patch(
        f"/v1/people/{person['person_id']}/descriptions/general",
        headers=_headers(),
        json={"expected_version": 0, "text": "x" * 501},
    )
    assert res.status_code == 422


def test_patch_release_returns_to_assistant(tmp_path):
    app = _app(tmp_path)
    person = _person(app.state.people)
    client = TestClient(app)
    client.patch(
        f"/v1/people/{person['person_id']}/descriptions/general",
        headers=_headers(),
        json={"expected_version": 0, "text": "Human owned."},
    )
    res = client.patch(
        f"/v1/people/{person['person_id']}/descriptions/general",
        headers=_headers(),
        json={"expected_version": 1, "release": True},
    )
    assert res.status_code == 200
    assert res.json()["descriptions"]["general"]["authority"] == "assistant"


def test_patch_unchanged_does_not_bump(tmp_path):
    app = _app(tmp_path)
    person = _person(app.state.people)
    client = TestClient(app)
    client.patch(
        f"/v1/people/{person['person_id']}/descriptions/general",
        headers=_headers(),
        json={"expected_version": 0, "text": "Same."},
    )
    res = client.patch(
        f"/v1/people/{person['person_id']}/descriptions/general",
        headers=_headers(),
        json={"expected_version": 1, "text": "Same."},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["saved"] is False
    assert body["unchanged"] is True
    assert body["descriptions"]["general"]["version"] == 1


def test_board_rows_include_descriptions(tmp_path):
    app = _app(tmp_path)
    person = _person(app.state.people)
    client = TestClient(app)
    client.patch(
        f"/v1/people/{person['person_id']}/descriptions/general",
        headers=_headers(),
        json={"expected_version": 0, "text": "On the board."},
    )
    board = client.get("/v1/board", headers=_headers()).json()
    rows = [row for lane in board.values() for row in lane]
    match = next(r for r in rows if r["person_id"] == person["person_id"])
    assert match["descriptions"]["general"]["text"] == "On the board."
