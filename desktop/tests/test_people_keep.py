from coworker.people import PersonStore
from coworker.tools import execute


def _alyssa() -> dict:
    return {
        "apolloId": "abc123",
        "firstName": "Alyssa",
        "lastNameObfuscated": "W***n",
        "title": "Partner",
        "organizationName": "Codeology",
        "hasEmail": True,
    }


def test_keep_one_apollo_row_files_person_without_email(tmp_path):
    people = PersonStore(tmp_path)
    ok, result = execute(
        "people_keep",
        {"people": [_alyssa()], "target": "club research dinner"},
        people=people,
    )
    assert ok is True
    kept = result["kept"]
    assert len(kept) == 1
    assert "email" not in kept[0]
    person = people.get(kept[0]["person_id"])
    assert person is not None
    assert person["first_name"] == "Alyssa"
    assert person["last_name"] == "W***n"
    assert person["title"] == "Partner"
    assert person["company"] == "Codeology"
    assert person["target"] == "club research dinner"
    assert person["email"] is None
    assert person["sequence_state"] is None


def test_keep_row_with_blank_apollo_fields_keeps_what_the_file_has(tmp_path):
    people = PersonStore(tmp_path)
    ok, first = execute(
        "people_keep",
        {"people": [_alyssa()], "target": "club research dinner"},
        people=people,
    )
    assert ok is True
    person_id = first["kept"][0]["person_id"]

    ok, again = execute(
        "people_keep",
        {
            "people": [
                {
                    "apolloId": "abc123",
                    "firstName": "Alyssa",
                    "lastNameObfuscated": "",
                    "title": "",
                    "organizationName": "   ",
                }
            ],
            "target": "club research dinner",
        },
        people=people,
    )
    assert ok is True
    assert again["kept"][0]["person_id"] == person_id
    assert again["kept"][0]["title"] == "Partner"
    assert again["kept"][0]["company"] == "Codeology"

    person = people.get(person_id)
    assert person["last_name"] == "W***n"
    assert person["title"] == "Partner"
    assert person["company"] == "Codeology"
    assert person["target"] == "club research dinner"


def test_keep_same_apollo_id_twice_does_not_fork(tmp_path):
    people = PersonStore(tmp_path)
    first = execute(
        "people_keep",
        {"people": [_alyssa()], "target": "initial outreach"},
        people=people,
    )[1]["kept"][0]
    ok, result = execute(
        "people_keep",
        {
            "people": [
                {
                    **_alyssa(),
                    "title": "General Partner",
                    "lastNameObfuscated": "Wilson",
                }
            ],
            "target": "follow up",
        },
        people=people,
    )
    assert ok is True
    assert len(result["kept"]) == 1
    assert result["kept"][0]["person_id"] == first["person_id"]
    loaded = people.get_by_apollo_id("abc123")
    assert loaded is not None
    assert loaded["person_id"] == first["person_id"]
    assert loaded["title"] == "General Partner"
    assert loaded["email"] is None


def test_keep_two_rows_returns_two_person_ids(tmp_path):
    people = PersonStore(tmp_path)
    ok, result = execute(
        "people_keep",
        {
            "people": [
                _alyssa(),
                {
                    "apolloId": "ada",
                    "firstName": "Ada",
                    "lastNameObfuscated": "L***e",
                    "title": "Founder",
                    "organizationName": "Analytic",
                },
            ],
            "target": "director-authored target",
        },
        people=people,
    )
    assert ok is True
    ids = [row["person_id"] for row in result["kept"]]
    assert len(ids) == 2
    assert len(set(ids)) == 2
    for row in result["kept"]:
        assert "email" not in row
        stored = people.get(row["person_id"])
        assert stored is not None
        assert stored["email"] is None


def test_keep_empty_list_succeeds(tmp_path):
    people = PersonStore(tmp_path)
    ok, result = execute("people_keep", {"people": []}, people=people)
    assert ok is True
    assert result["kept"] == []


def test_people_keep_reports_partial_rows_without_losing_success(tmp_path):
    people = PersonStore(tmp_path)

    ok, result = execute(
        "people_keep",
        {
            "people": [_alyssa(), {"firstName": "Missing Apollo identity"}],
            "target": "Director-authored target",
        },
        people=people,
    )

    assert ok is True
    assert result["status"] == "partial"
    assert result["selected_row_count"] == 2
    assert [row["apollo_id"] for row in result["kept"]] == ["abc123"]
    assert result["failed"] == [
        {"row_index": 1, "apollo_id": None, "code": "missing_apollo_id"}
    ]


def test_people_keep_is_auto():
    from coworker.permissions import decide

    decision = decide("people_keep")
    assert decision.allowed is True
    assert decision.needs_user is False


def test_versioned_prompt_and_tool_catalog_support_keep_after_search(tmp_path):
    from coworker.server import system_prompt
    from coworker.store import ConversationStore
    from coworker.tools import OPENAI_TOOLS

    tool_names = {schema["function"]["name"] for schema in OPENAI_TOOLS}
    prompt = system_prompt(ConversationStore(tmp_path))
    assert "people_keep" in tool_names
    assert "search for People" in prompt
    assert "never invent" in prompt


def test_people_keep_schema_requires_the_director_target():
    from coworker.tools import PEOPLE_KEEP_SCHEMA

    assert PEOPLE_KEEP_SCHEMA["function"]["parameters"]["required"] == [
        "people",
        "target",
    ]
