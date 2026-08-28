"""A send whose response never came back is not a send that failed.

`agent_run_dispatch` exists to keep two facts apart: "it failed" and "we do not
know". PR #124 wired the fence around the send paths, but both of them catch
every exception the Gmail client raises and return `ok=False`, so the second
fact was destroyed one layer below the fence and never reached it.

The distinction is only decidable where the HTTP call happens. A status code
means Gmail answered, and an answer is a fact. No status at all means the
request left this machine and nothing came back, which is the whole ambiguous
window the run store was built for.
"""

from __future__ import annotations

import httpx
import pytest

from fastapi.testclient import TestClient

from coworker.agent_run_approval import EffectStatus
from coworker.apollo import FakeHttp
from coworker.gmail import (
    DRAFTS_URL,
    GmailApi,
    GmailError,
    GmailOutcomeUnknown,
)
from coworker.gmail import FakeGmail
from coworker.secrets import SecretStore
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-ambiguous-send"
HEADERS = {TOKEN_HEADER: TOKEN}
ACCOUNT = "director@sourcecado.test"
ADA_EMAIL = "ada@analytic.example"
SUBJECT = "Thursday?"
BODY = "Hi Ada,\n\nWould Thursday work for a short call?\n\nFisher"

SEND_URL = f"{DRAFTS_URL}/send"


def _api(tmp_path, routes) -> GmailApi:
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("gmail", {"refresh_token": "rt", "access_token": "at"})
    return GmailApi(
        secrets, http=FakeHttp(routes), client_id="cid", client_secret="sec"
    )


def _timeout() -> httpx.ReadTimeout:
    """What httpx raises when the request went out and the read expired."""
    return httpx.ReadTimeout("timed out", request=httpx.Request("POST", SEND_URL))


def _rejected(status: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", SEND_URL)
    response = httpx.Response(status, json={"error": {"message": message}}, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


def test_a_send_whose_response_never_arrived_says_the_outcome_is_unknown(tmp_path):
    api = _api(tmp_path, {SEND_URL: _timeout()})
    with pytest.raises(GmailOutcomeUnknown):
        api.send(draft_id="draft_1")


def test_a_send_gmail_rejected_is_a_plain_failure_and_not_an_unknown(tmp_path):
    """Gmail answered with a status. Nothing was sent and the code may say so."""
    api = _api(tmp_path, {SEND_URL: _rejected(403, "Request had insufficient scopes.")})
    with pytest.raises(GmailError) as raised:
        api.send(draft_id="draft_1")
    assert not isinstance(raised.value, GmailOutcomeUnknown)
    assert "insufficient scopes" in str(raised.value)


def test_a_timeout_while_checking_the_draft_is_not_an_unknown_send(tmp_path):
    """The binding check reads. It runs before the send, so nothing is in doubt.

    `draft_snapshot` already converts anything `get_draft` raises into
    `SendAuthorityError`. This pins that, because the new unknown must not
    escape through a read and put a send that never happened into review.
    """
    from coworker.gmail import SendAuthority, SendAuthorityError, send_reviewed_draft

    class UnreachableOnRead:
        drafts: list = []
        sends: list = []

        def get_draft(self, *, draft_id: str):
            raise GmailOutcomeUnknown("Gmail did not answer, so the outcome is unknown.")

        def send(self, *, draft_id: str):  # pragma: no cover - must never run
            raise AssertionError("the send must not be attempted")

    authority = SendAuthority(
        approval_id="a1",
        person_id="p1",
        draft_id="draft_1",
        account="director@sourcecado.test",
        to="ada@analytic.example",
        subject="Thursday?",
        body_digest="deadbeef",
    )
    with pytest.raises(SendAuthorityError) as raised:
        send_reviewed_draft(UnreachableOnRead(), authority)
    assert not isinstance(raised.value, GmailOutcomeUnknown)
    assert raised.value.code == "draft_unavailable"


# ==========================================================================
# The approval path: the fence must see the unknown
# ==========================================================================


class TimingOutGmail(FakeGmail):
    """The send request left the machine. The read expired.

    `send_attempts` still records the attempt, because the attempt is the fact
    that makes the outcome ambiguous.
    """

    def send(self, *, draft_id: str):
        self.send_attempts.append(draft_id)
        raise GmailOutcomeUnknown("Gmail did not answer, so the outcome is unknown.")


def _approved_send_that_times_out(tmp_path):
    """Park one reviewed send, allow it, and let the send time out."""
    gmail = TimingOutGmail()
    gmail.account_email = ACCOUNT
    app = create_app(token=TOKEN, provider=None, state=tmp_path, gmail=gmail)
    client = TestClient(app)
    people = app.state.people
    person = people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L.",
        title="Head of Data",
        company="Analytic",
    )
    people.apply_enrichment(person["person_id"], name="Ada Lovelace", email=ADA_EMAIL)
    person = people.get(person["person_id"])
    person_id = person["person_id"]
    session_id = app.state.store.create_session()["session_id"]
    people.bind_session(
        session_id, person_id, expected_person_version=int(person["version"])
    )
    drafted = client.post(
        f"/v1/people/{person_id}/outreach/draft",
        headers=HEADERS,
        json={"session_id": session_id, "subject": SUBJECT, "body": BODY},
    ).json()["draft"]
    item_id = client.post(
        f"/v1/people/{person_id}/outreach/send-approval",
        headers=HEADERS,
        json={
            "session_id": session_id,
            "draft_id": drafted["id"],
            "reviewed_body_digest": drafted["body_digest"],
        },
    ).json()["item"]["id"]
    client.post(
        f"/v1/inbox/{item_id}",
        headers=HEADERS,
        json={"decision": "allow", "actor": "Fisher", "scope": "once"},
    )
    return app, client, item_id


def test_an_approved_send_that_times_out_is_held_for_review_not_called_failed(tmp_path):
    app, client, item_id = _approved_send_that_times_out(tmp_path)

    # Non-vacuity: the request really did leave the machine.
    assert app.state.gmail.send_attempts == ["draft_1"]

    queue = client.get("/v1/agent-run-effects/quarantine", headers=HEADERS).json()
    held = [(row["tool_name"], row["status"]) for row in queue["effects"]]
    assert held == [("gmail_send", str(EffectStatus.AMBIGUOUS))]
    assert queue["effects"][0]["needs_a_person"] is True


def test_the_operator_is_told_the_outcome_is_unknown_not_that_it_failed(tmp_path):
    app, client, item_id = _approved_send_that_times_out(tmp_path)
    receipt = app.state.store.get_inbox(item_id)
    assert "unknown" in str(receipt.get("execution_error") or "").lower()
    assert "held for review" in str(receipt.get("execution_error") or "").lower()


# ==========================================================================
# The turn loop: same fact, same treatment
# ==========================================================================


def test_the_gmail_send_tool_does_not_turn_an_unknown_into_ok_false():
    """`execute` is the other side of the fence and swallowed this too.

    A tool result of `ok=False` is a statement that nothing happened. The tool
    layer cannot make that statement about a request Gmail never answered, so
    the exception carries on to `guarded_call`.
    """
    from coworker.tools import execute

    gmail = TimingOutGmail()
    gmail.account_email = ACCOUNT
    with pytest.raises(GmailOutcomeUnknown):
        execute("gmail_send", {"draft_id": "draft_1"}, gmail=gmail)
    assert gmail.send_attempts == ["draft_1"]


def test_a_send_gmail_rejected_is_still_an_ordinary_tool_failure():
    """The inverse guard. A rejection is a fact and stays a plain failure."""
    from coworker.tools import execute

    class RejectingGmail(FakeGmail):
        def send(self, *, draft_id: str):
            raise GmailError("Request had insufficient scopes.")

    ok, result = execute("gmail_send", {"draft_id": "draft_1"}, gmail=RejectingGmail())
    assert ok is False
    assert "insufficient scopes" in result["error"]


def test_a_turn_whose_send_outcome_is_unknown_does_not_tell_the_operator_it_failed(
    tmp_path,
):
    """The whole point, at the surface the director actually reads.

    The approval path already maps this to `outcome_unknown`. The turn loop let
    the exception reach the generic handler, which wrote `state: "failed"` and
    printed the internal sentence, effect id and all.
    """
    import asyncio

    from coworker.agent_run_repository import AgentRunRepository
    from coworker.inbox import Inbox
    from coworker.people import PersonStore
    from coworker.provider import StreamChunk, ToolCall
    from coworker.store import ConversationStore
    from coworker.tools import OPENAI_TOOLS
    from coworker.turn import run_turn

    class AsksToSend:
        provider_id = "fake"
        model_id = "fake"

        async def astream(self, *, messages, tools=None, context_id=None):
            yield StreamChunk.started(provider=self.provider_id, model=self.model_id)
            yield StreamChunk(text_delta="Sending. ")
            yield StreamChunk(finish_reason="tool_calls")
            yield StreamChunk(
                tool_calls=[
                    ToolCall(
                        id="call_send",
                        name="gmail_send",
                        arguments={"draft_id": "draft_1"},
                    )
                ]
            )

    gmail = TimingOutGmail()
    gmail.account_email = ACCOUNT
    repository = AgentRunRepository(tmp_path / "runs")
    owner = repository.registry.register()
    conversations = ConversationStore(tmp_path / "conv")
    seen: list[dict] = []

    async def _emit(event):
        seen.append(event)

    async def _allow(_call_id):
        return "allow"

    asyncio.run(
        run_turn(
            text="Send the note.",
            sid="sess-unknown-outcome",
            store=conversations,
            provider=AsksToSend(),
            persona=None,
            skills=None,
            inbox=Inbox(conversations),
            openai_tools=OPENAI_TOOLS,
            execute_kwargs={
                "people": PersonStore(tmp_path / "people"),
                "gmail": gmail,
            },
            emit=_emit,
            wait_permission=_allow,
            agent_runs=repository,
            run_owner=owner,
        )
    )

    # Non-vacuity: the send really was attempted and really was fenced.
    assert gmail.send_attempts == ["draft_1"]
    held = repository.list_quarantined_effects()
    assert [row["tool_name"] for row in held] == ["gmail_send"]

    terminal = [
        event
        for event in seen
        if event.get("type") in {"error", "turn_end", "turn_stopped"}
    ]
    assert len(terminal) == 1
    event = terminal[0]
    assert event["state"] == "held"
    assert event["code"] == "outcome_unknown"
    assert event["effect_id"] == held[0]["effect_id"]
    assert "held for review" in event["message"]
    # The internal sentence, and the bare effect id inside it, stay internal.
    assert "never reported an outcome" not in event["message"]
