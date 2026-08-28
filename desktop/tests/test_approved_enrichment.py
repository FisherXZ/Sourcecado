"""S3: deliberate Apollo enrichment for one bound person.

Apollo credits are the money in this flow, so the counter that matters is
``http.calls`` — the real requests that left. No test here reads a status field
to decide whether a credit was spent.
"""

from fastapi.testclient import TestClient

from coworker.apollo import MATCH_URL, FakeHttp
from coworker.gmail import FakeGmail
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-approved-enrich"
HEADERS = {TOKEN_HEADER: TOKEN}

APOLLO_PERSON = {
    "person": {
        "id": "apollo_person_77",
        "name": "Ada Lovelace",
        "title": "Head of Data",
        "organization": {"name": "Analytic Engines"},
        "linkedin_url": "https://www.linkedin.com/in/ada",
        "email": "ada@analytic.example",
        "phone_numbers": [{"raw_number": "+1 555 0100"}],
    }
}


def _app(tmp_path, http):
    return create_app(
        token=TOKEN,
        provider=None,
        state=tmp_path,
        gmail=FakeGmail(),
        http=http,
        apollo_key="test-key",
    )


def _person(app, *, apollo_id, first, last, company="Analytic"):
    people = app.state.people
    person = people.keep_from_apollo(
        apollo_id=apollo_id,
        first_name=first,
        last_name_obfuscated=last,
        title="Head of Data",
        company=company,
    )
    session_id = app.state.store.create_session()["session_id"]
    people.bind_session(
        session_id, person["person_id"], expected_person_version=int(person["version"])
    )
    return person["person_id"], session_id


def _park(client, person_id, session_id, **overrides):
    payload = {"session_id": session_id}
    payload.update(overrides)
    return client.post(
        f"/v1/people/{person_id}/enrich-approval", headers=HEADERS, json=payload
    )


def _decide(client, item_id, decision="allow"):
    return client.post(
        f"/v1/inbox/{item_id}",
        headers=HEADERS,
        json={"decision": decision, "actor": "Fisher", "scope": "once"},
    )


def _match_calls(http):
    return [call for call in http.calls if call["url"] == MATCH_URL]


# --------------------------------------------------------------------------
# Criterion 2 — the approval shows the exact person and the credit spend
# --------------------------------------------------------------------------


def test_the_enrichment_approval_names_the_person_and_the_credit_cost(tmp_path):
    http = FakeHttp({MATCH_URL: APOLLO_PERSON})
    app = _app(tmp_path, http)
    client = TestClient(app)
    person_id, session_id = _person(app, apollo_id="ada", first="Ada", last="Lovelace")

    res = _park(client, person_id, session_id)

    assert res.status_code == 201, res.text
    resource = res.json()["item"]["resource"]
    assert resource["kind"] == "apollo_enrichment"
    assert resource["person_id"] == person_id
    assert resource["person"] == "Ada Lovelace"
    assert resource["company"] == "Analytic"
    assert resource["credits"] == 1
    assert resource["matched_on"] == "name"
    assert "1 Apollo credit" in resource["reason"]
    assert "Ada Lovelace" in resource["reason"]
    # Parking an approval spends nothing.
    assert _match_calls(http) == []


def test_an_enrichment_cannot_start_from_another_persons_chat(tmp_path):
    http = FakeHttp({MATCH_URL: APOLLO_PERSON})
    app = _app(tmp_path, http)
    client = TestClient(app)
    person_id, session_id = _person(app, apollo_id="ada", first="Ada", last="Lovelace")
    _other, other_session = _person(
        app, apollo_id="grace", first="Grace", last="Hopper"
    )

    unbound = _park(client, person_id, session_id=None)
    wrong = _park(client, person_id, other_session)

    assert unbound.status_code == 400
    assert wrong.status_code == 409
    assert wrong.json()["code"] == "unbound_session"
    assert app.state.inbox.pending() == []
    assert _match_calls(http) == []
    # The control: this person's own chat parks an approval.
    assert _park(client, person_id, session_id).status_code == 201


def test_a_person_apollo_cannot_be_matched_on_is_refused_before_the_spend(tmp_path):
    http = FakeHttp({MATCH_URL: APOLLO_PERSON})
    app = _app(tmp_path, http)
    client = TestClient(app)
    person_id, session_id = _person(app, apollo_id="ada", first="Ada", last=None)

    res = _park(client, person_id, session_id)

    assert res.status_code == 409
    assert res.json()["code"] == "no_match_key"
    assert _match_calls(http) == []


# --------------------------------------------------------------------------
# Criterion 3 — one person updated, one Apollo source receipt
# --------------------------------------------------------------------------


def test_an_approved_enrichment_updates_only_that_person_and_files_the_receipt(
    tmp_path,
):
    http = FakeHttp({MATCH_URL: APOLLO_PERSON})
    app = _app(tmp_path, http)
    client = TestClient(app)
    person_id, session_id = _person(app, apollo_id="ada", first="Ada", last="Lovelace")
    other_id, _ = _person(app, apollo_id="grace", first="Grace", last="Hopper")
    before_other = app.state.people.get(other_id)
    item_id = _park(client, person_id, session_id, run_id="run-enrich-1").json()["item"][
        "id"
    ]

    res = _decide(client, item_id)

    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    assert len(_match_calls(http)) == 1
    assert _match_calls(http)[0]["json"]["first_name"] == "Ada"
    assert _match_calls(http)[0]["json"]["last_name"] == "Lovelace"

    person = app.state.people.get(person_id, expand_sources=True)
    assert person["email"] == "ada@analytic.example"
    assert person["linkedin_url"] == "https://www.linkedin.com/in/ada"
    assert person["company"] == "Analytic Engines"
    # Only the approved person moved.
    assert app.state.people.get(other_id) == before_other

    sources = [row for row in person["sources"] if row["fields"]["source"] == "apollo"]
    assert len(sources) == 1
    fields = sources[0]["fields"]
    assert fields["apollo_id"] == "apollo_person_77"
    assert fields["approval_id"] == item_id
    assert fields["credits"] == 1
    assert fields["matched_on"] == "name"
    assert fields["fields_applied"] == [
        "company",
        "email",
        "linkedin_url",
        "name",
        "phone",
        "title",
    ]

    enrich_events = [
        row for row in app.state.people.timeline(person_id) if row["kind"] == "enrich"
    ]
    assert len(enrich_events) == 1
    assert enrich_events[0]["payload"]["approval_id"] == item_id
    assert enrich_events[0]["payload"]["credits"] == 1
    assert enrich_events[0]["run_id"] == "run-enrich-1"
    assert enrich_events[0]["session_id"] == session_id


def test_a_denied_enrichment_spends_no_credit(tmp_path):
    http = FakeHttp({MATCH_URL: APOLLO_PERSON})
    app = _app(tmp_path, http)
    client = TestClient(app)
    person_id, session_id = _person(app, apollo_id="ada", first="Ada", last="Lovelace")
    item_id = _park(client, person_id, session_id).json()["item"]["id"]
    assert app.state.inbox.get(item_id)["state"] == "pending"

    res = _decide(client, item_id, "deny")

    # The denial was really recorded; this is not a route that never ran.
    assert res.json()["item"]["decision"] == "deny"
    assert res.json()["item"]["execution_status"] == "not_run"
    assert _match_calls(http) == []
    assert app.state.people.get(person_id)["email"] is None
    assert app.state.people.get(person_id, expand_sources=True)["sources"] == []


def test_a_duplicate_enrichment_submission_spends_one_credit(tmp_path):
    http = FakeHttp({MATCH_URL: APOLLO_PERSON})
    app = _app(tmp_path, http)
    client = TestClient(app)
    person_id, session_id = _person(app, apollo_id="ada", first="Ada", last="Lovelace")
    item_id = _park(client, person_id, session_id).json()["item"]["id"]

    first = _decide(client, item_id)
    second = _decide(client, item_id)

    assert first.json()["ok"] is True
    assert second.json()["idempotent"] is True
    assert second.json()["result"] == first.json()["result"]
    assert len(_match_calls(http)) == 1
    person = app.state.people.get(person_id, expand_sources=True)
    assert len([row for row in person["sources"] if row["fields"]["source"] == "apollo"]) == 1


def test_an_expired_enrichment_approval_spends_no_credit(tmp_path):
    http = FakeHttp({MATCH_URL: APOLLO_PERSON})
    app = _app(tmp_path, http)
    app.state.store.approval_ttl_seconds = 0.05
    client = TestClient(app)
    person_id, session_id = _person(app, apollo_id="ada", first="Ada", last="Lovelace")
    item_id = _park(client, person_id, session_id).json()["item"]["id"]
    import time

    time.sleep(0.12)

    res = _decide(client, item_id)

    assert res.status_code == 409
    expired = app.state.inbox.get(item_id)
    assert expired["state"] == "expired"
    assert expired["decision"] is None
    assert _match_calls(http) == []
    assert app.state.people.get(person_id)["email"] is None
