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


# --- Saved-person finder search (#242) ---


def _kept(store, apollo_id, first, last, title, company):
    return store.keep_from_apollo(
        apollo_id=apollo_id,
        first_name=first,
        last_name_obfuscated=last,
        title=title,
        company=company,
    )


def test_search_finds_both_kept_backlog_and_active_people_with_lane(tmp_path):
    store = PersonStore(tmp_path)
    kept = _kept(store, "a1", "Maya", "Chen", "Engineer", "Acme Labs")
    active = _kept(store, "a2", "Noor", "Ali", "Designer", "Acme Labs")
    store.set_sequence(active["person_id"], "open", actor="director")

    results = store.search("acme")

    by_id = {row["person_id"]: row for row in results}
    assert kept["person_id"] in by_id
    assert active["person_id"] in by_id
    assert by_id[kept["person_id"]]["board_lane"] == "backlog"
    assert by_id[kept["person_id"]]["sequence_state"] is None
    assert by_id[active["person_id"]]["board_lane"] == "open"
    assert by_id[active["person_id"]]["sequence_state"] == "open"


def test_search_excludes_deleted_people(tmp_path):
    store = PersonStore(tmp_path)
    person = _kept(store, "a1", "Maya", "Chen", "Engineer", "Acme Labs")
    store.delete(
        person["person_id"],
        expected_version=person["version"],
        actor="director",
        rationale_summary="duplicate",
    )

    assert store.search("maya") == []
    assert store.search("acme") == []


def test_search_returns_both_people_with_the_same_name(tmp_path):
    store = PersonStore(tmp_path)
    one = _kept(store, "a1", "Alex", "Rao", "PM", "Acme")
    two = _kept(store, "a2", "Alex", "Rao", "PM", "Globex")

    results = store.search("alex rao")
    ids = {row["person_id"] for row in results}
    assert ids == {one["person_id"], two["person_id"]}

    # A company token narrows the duplicate name to one person.
    narrowed = store.search("alex globex")
    assert [row["person_id"] for row in narrowed] == [two["person_id"]]


def test_search_matches_masked_surname_person_without_using_the_mask(tmp_path):
    store = PersonStore(tmp_path)
    person = _kept(store, "a1", "Alyssa", "W***n", "Partner", "Codeology")

    # Found by first name and company, which are real identity data.
    assert person["person_id"] in {r["person_id"] for r in store.search("alyssa")}
    assert person["person_id"] in {r["person_id"] for r in store.search("codeology")}
    # The redacted surname fragment is never a usable match key.
    assert store.search("w**n") == []
    hit = store.search("alyssa")[0]
    assert hit["last_name"] is None
    assert hit["last_name_status"] == "hidden_by_apollo"
    assert "W***n" not in str(store.search("alyssa"))


def test_search_known_email_matches_exactly_and_never_returns_the_address(tmp_path):
    store = PersonStore(tmp_path)
    person = _kept(store, "a1", "Maya", "Chen", "Engineer", "Acme Labs")
    store.apply_enrichment(person["person_id"], email="maya@acme.test")
    other = _kept(store, "a2", "Noor", "Ali", "Designer", "Globex")

    exact = store.search("MAYA@ACME.TEST")
    assert [row["person_id"] for row in exact] == [person["person_id"]]
    assert exact[0]["match_kind"] == "email"
    assert exact[0]["has_email"] is True
    # The stored address must never appear anywhere in search output.
    assert "maya@acme.test" not in str(store.search("maya")).lower()
    # A partial email is not a match (exact-only), and no address leaks.
    assert store.search("maya@acme") == []
    # Someone without an email reports the state, not an address.
    no_email = [r for r in store.search("noor") if r["person_id"] == other["person_id"]][0]
    assert no_email["has_email"] is False


def test_search_is_bounded_by_limit_and_a_hard_cap(tmp_path):
    store = PersonStore(tmp_path)
    for index in range(60):
        _kept(store, f"a{index}", "Sam", f"Num{index}", "Analyst", "Acme Labs")

    assert len(store.search("acme", limit=20)) == 20
    assert len(store.search("acme", limit=999)) == 50  # clamped to SEARCH_LIMIT_MAX
    assert len(store.search("acme", limit=0)) == 1  # clamped up to at least 1


def test_search_empty_query_returns_nothing(tmp_path):
    store = PersonStore(tmp_path)
    _kept(store, "a1", "Maya", "Chen", "Engineer", "Acme Labs")
    assert store.search("") == []
    assert store.search("   ") == []


def test_search_runs_on_a_bare_local_store_without_any_enrichment_client(tmp_path):
    # PersonStore needs only a local directory — no Apollo/Gmail client — so
    # search() structurally cannot fetch an email or spend an enrichment credit.
    store = PersonStore(tmp_path)
    _kept(store, "a1", "Maya", "Chen", "Engineer", "Acme Labs")
    results = store.search("maya")
    assert [row["match_kind"] for row in results] == ["name"]


def test_resolve_shortlist_marks_saved_apollo_ids_and_skips_deleted(tmp_path):
    store = PersonStore(tmp_path)
    kept = _kept(store, "live1", "Maya", "Chen", "Engineer", "Acme Labs")
    gone = _kept(store, "dead1", "Noor", "Ali", "Designer", "Globex")
    store.delete(
        gone["person_id"],
        expected_version=gone["version"],
        actor="director",
        rationale_summary="removed",
    )

    out = store.resolve_shortlist(
        [
            {"apollo_id": "live1", "name": "Maya Chen"},
            {"apollo_id": "dead1", "name": "Noor Ali"},
            {"apollo_id": "unknown1", "name": "Nobody Here"},
        ]
    )

    assert set(out["resolved"]) == {"live1"}
    assert out["resolved"]["live1"]["person_id"] == kept["person_id"]
    assert out["resolved"]["live1"]["board_lane"] == "backlog"
    # A deleted person's id is neither resolved nor a possible match.
    assert "dead1" not in out["resolved"]
    assert out["possible"] == {}


def test_resolve_shortlist_flags_name_only_candidate_as_possible_not_resolved(tmp_path):
    store = PersonStore(tmp_path)
    saved = _kept(store, "saved-apollo", "Maya", "Chen", "Engineer", "Acme Labs")

    # A different Apollo id but the same full name -> possible match, never merged.
    out = store.resolve_shortlist(
        [{"apollo_id": "other-apollo", "name": "maya  CHEN"}]
    )

    assert out["resolved"] == {}
    assert [m["person_id"] for m in out["possible"]["other-apollo"]] == [
        saved["person_id"]
    ]

    # The same candidate whose id IS saved is "Already saved", not a possible match.
    same = store.resolve_shortlist([{"apollo_id": "saved-apollo", "name": "Maya Chen"}])
    assert set(same["resolved"]) == {"saved-apollo"}
    assert same["possible"] == {}


def test_resolve_shortlist_does_not_name_match_a_masked_surname(tmp_path):
    store = PersonStore(tmp_path)
    _kept(store, "masked", "Alyssa", "W***n", "Partner", "Codeology")

    # The saved person has no canonical surname, so a full-name candidate can't
    # produce a false "possible match".
    out = store.resolve_shortlist([{"apollo_id": "cand", "name": "Alyssa Wilson"}])
    assert out["possible"] == {}
