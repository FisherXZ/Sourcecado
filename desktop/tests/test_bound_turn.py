import asyncio

from coworker.gmail import FakeGmail
from coworker.inbox import Inbox
from coworker.people import PersonStore
from coworker.provider import FakeProvider, ToolCall
from coworker.store import ConversationStore
from coworker.tools import OPENAI_TOOLS
from coworker.turn import run_turn


def _alyssa() -> dict:
    return {
        "apolloId": "abc123",
        "firstName": "Alyssa",
        "lastNameObfuscated": "W***n",
        "title": "Partner",
        "organizationName": "Codeology",
    }


def _run(*, tmp_path, sid, text, provider, people, gmail=None, drive=None, wait=None, http=None, apollo_key=None):
    conv = ConversationStore(tmp_path)

    async def _wait(_call_id: str) -> str:
        return wait or "allow"

    return asyncio.run(
        run_turn(
            text=text,
            sid=sid,
            store=conv,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(conv),
            openai_tools=OPENAI_TOOLS,
            execute_kwargs={
                "people": people,
                "gmail": gmail,
                "drive": drive,
                "http": http,
                "apollo_key": apollo_key,
            },
            wait_permission=_wait if wait is not None else None,
        )
    )


def test_keep_one_person_binds_that_session_only(tmp_path):
    people = PersonStore(tmp_path)
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="keep_1",
                        name="people_keep",
                        arguments={"people": [_alyssa()], "target": "dinner"},
                    )
                ]
            },
            {"deltas": ("Kept Alyssa.",)},
        ]
    )
    _run(
        tmp_path=tmp_path,
        sid="sess-a",
        text="keep Alyssa",
        provider=fake,
        people=people,
    )
    person_id = people.person_for_session("sess-a")
    assert person_id is not None
    assert people.get(person_id)["first_name"] == "Alyssa"
    assert people.person_for_session("sess-b") is None


def _ada() -> dict:
    return {
        "apolloId": "ada",
        "firstName": "Ada",
        "lastNameObfuscated": "L",
        "title": "Founder",
        "organizationName": "Analytic",
    }


def _alonzo() -> dict:
    return {
        "apolloId": "alonzo",
        "firstName": "Alonzo",
        "lastNameObfuscated": "C",
        "title": "Professor",
        "organizationName": "Princeton",
    }


class _SearchGmail:
    def search(self, query: str, max_results: int = 10) -> dict:
        return {
            "messages": [
                {"id": "m1", "from": "ada@analytic.example", "subject": "Hello"}
            ]
        }


def _keep(tmp_path, people, sid, row):
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="keep_1",
                        name="people_keep",
                        arguments={"people": [row]},
                    )
                ]
            },
            {"deltas": ("Kept.",)},
        ]
    )
    _run(tmp_path=tmp_path, sid=sid, text="keep", provider=fake, people=people)


def test_bound_gmail_search_files_on_ada_not_alonzo(tmp_path):
    people = PersonStore(tmp_path)
    _keep(tmp_path, people, "sess-ada", _ada())
    _keep(tmp_path, people, "sess-alonzo", _alonzo())
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="search_1",
                        name="gmail_search",
                        arguments={"query": "from:ada"},
                    )
                ]
            },
            {"deltas": ("Found mail.",)},
        ]
    )
    _run(
        tmp_path=tmp_path,
        sid="sess-ada",
        text="search mail",
        provider=fake,
        people=people,
        gmail=_SearchGmail(),
    )
    ada = people.get_by_apollo_id("ada")
    alonzo = people.get_by_apollo_id("alonzo")
    assert ada is not None and alonzo is not None
    kinds = [row["kind"] for row in people.timeline(ada["person_id"])]
    assert "mail" in kinds
    assert people.timeline(alonzo["person_id"]) == []


def test_unbound_gmail_search_does_not_file(tmp_path):
    people = PersonStore(tmp_path)
    _keep(tmp_path, people, "sess-ada", _ada())
    ada = people.get_by_apollo_id("ada")
    assert ada is not None
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="search_1",
                        name="gmail_search",
                        arguments={"query": "from:ada"},
                    )
                ]
            },
            {"deltas": ("Found mail.",)},
        ]
    )
    _run(
        tmp_path=tmp_path,
        sid="sess-other",
        text="search mail",
        provider=fake,
        people=people,
        gmail=_SearchGmail(),
    )
    assert people.person_for_session("sess-other") is None
    assert people.timeline(ada["person_id"]) == []


def test_bound_failed_drive_read_files_error(tmp_path):
    people = PersonStore(tmp_path)
    _keep(tmp_path, people, "sess-ada", _ada())
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="read_1",
                        name="drive_read",
                        arguments={"file_id": "d1"},
                    )
                ]
            },
            {"deltas": ("Drive is down.",)},
        ]
    )
    _run(
        tmp_path=tmp_path,
        sid="sess-ada",
        text="read the deck",
        provider=fake,
        people=people,
        drive=None,
    )
    ada = people.get_by_apollo_id("ada")
    assert ada is not None
    timeline = people.timeline(ada["person_id"])
    assert [row["kind"] for row in timeline] == ["error"]
    assert timeline[0]["source"] == "drive"
    assert people.get(ada["person_id"]) is not None


def test_keep_two_people_does_not_bind(tmp_path):
    people = PersonStore(tmp_path)
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="keep_2",
                        name="people_keep",
                        arguments={"people": [_ada(), _alonzo()]},
                    )
                ]
            },
            {"deltas": ("Kept two.",)},
        ]
    )
    _run(
        tmp_path=tmp_path,
        sid="sess-a",
        text="keep both",
        provider=fake,
        people=people,
    )
    assert people.person_for_session("sess-a") is None
    assert people.get_by_apollo_id("ada") is not None
    assert people.get_by_apollo_id("alonzo") is not None


def _draft_call():
    return ToolCall(
        id="draft_1",
        name="gmail_draft",
        arguments={
            "to": "ada@analytic.example",
            "subject": "Dinner",
            "body": "Would you join the research dinner?",
        },
    )


def test_allowed_draft_opens_sequence_and_does_not_send(tmp_path):
    people = PersonStore(tmp_path)
    _keep(tmp_path, people, "sess-ada", _ada())
    gmail = FakeGmail()
    fake = FakeProvider(
        steps=[
            {"tool_calls": [_draft_call()]},
            {"deltas": ("Draft is ready.",)},
        ]
    )
    _run(
        tmp_path=tmp_path,
        sid="sess-ada",
        text="write Ada",
        provider=fake,
        people=people,
        gmail=gmail,
        wait="allow",
    )
    ada = people.get_by_apollo_id("ada")
    assert ada is not None
    assert ada["sequence_state"] == "open"
    assert [row["person_id"] for row in people.list_board()["open"]] == [ada["person_id"]]
    drafts = [row for row in people.timeline(ada["person_id"]) if row["kind"] == "draft"]
    assert len(drafts) == 1
    assert drafts[0]["payload"]["to"] == "ada@analytic.example"
    assert drafts[0]["payload"]["subject"] == "Dinner"
    assert drafts[0]["payload"]["draft_id"] == "draft_1"
    assert drafts[0]["payload"]["sent"] is False
    assert gmail.sends == []
    assert len(gmail.drafts) == 1


def test_draft_does_not_reset_in_conversation(tmp_path):
    people = PersonStore(tmp_path)
    _keep(tmp_path, people, "sess-ada", _ada())
    ada = people.get_by_apollo_id("ada")
    assert ada is not None
    people.set_sequence(ada["person_id"], "in_conversation", actor="director")
    fake = FakeProvider(
        steps=[
            {"tool_calls": [_draft_call()]},
            {"deltas": ("Draft is ready.",)},
        ]
    )
    _run(
        tmp_path=tmp_path,
        sid="sess-ada",
        text="write again",
        provider=fake,
        people=people,
        gmail=FakeGmail(),
        wait="allow",
    )
    assert people.get(ada["person_id"])["sequence_state"] == "in_conversation"


def test_denied_draft_does_not_open_sequence(tmp_path):
    people = PersonStore(tmp_path)
    _keep(tmp_path, people, "sess-ada", _ada())
    gmail = FakeGmail()
    fake = FakeProvider(
        steps=[
            {"tool_calls": [_draft_call()]},
            {"deltas": ("Okay.",)},
        ]
    )
    _run(
        tmp_path=tmp_path,
        sid="sess-ada",
        text="write Ada",
        provider=fake,
        people=people,
        gmail=gmail,
        wait="deny",
    )
    ada = people.get_by_apollo_id("ada")
    assert ada is not None
    assert ada["sequence_state"] is None
    assert people.list_board()["open"] == []
    assert [row["kind"] for row in people.timeline(ada["person_id"]) if row["kind"] == "draft"] == []
    assert gmail.drafts == []
    assert gmail.sends == []
