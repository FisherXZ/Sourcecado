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
    # No email, no surname, and no Apollo ID: nothing identifies anybody.
    person_id, session_id = _person(app, apollo_id=None, first="Ada", last=None)

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


def test_verified_enrichment_promotes_the_full_name_across_person_surfaces(tmp_path):
    from coworker.server import system_prompt

    http = FakeHttp({MATCH_URL: APOLLO_PERSON})
    app = _app(tmp_path, http)
    client = TestClient(app)
    person_id, session_id = _person(
        app,
        apollo_id="apollo_person_77",
        first="Ada",
        last="L***e",
    )
    app.state.store.rename_session(session_id, "Sourcing · Ada L***e")
    parked = _park(client, person_id, session_id)
    assert "L***e" not in parked.text

    decided = _decide(client, parked.json()["item"]["id"])

    assert decided.status_code == 200, decided.text
    person = app.state.people.get(person_id)
    assert person is not None
    assert person["person_id"] == person_id
    assert person["last_name"] == "Lovelace"
    assert person["last_name_status"] == "known"
    assert "L***e" not in str(person)
    person_view = client.get(f"/v1/people/{person_id}", headers=HEADERS)
    assert person_view.status_code == 200
    assert "Ada Lovelace" in person_view.text
    assert "L***e" not in person_view.text
    prompt = system_prompt(
        app.state.store,
        people=app.state.people,
        session_id=session_id,
    )
    assert "Ada Lovelace" in prompt
    assert "L***e" not in prompt
    session = app.state.store.index(session_id)
    assert session is not None
    assert "Ada Lovelace" in str(session["title"])
    assert "L***e" not in str(session["title"])
    assert app.state.people.person_for_session(session_id) == person_id


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


# --------------------------------------------------------------------------
# Issue #126 — a masked surname must never reach Apollo, and a kept candidate
# must still be enrichable through the Apollo ID the shortlist already stored.
# --------------------------------------------------------------------------


def test_a_masked_surname_parks_no_approval_and_spends_nothing(tmp_path):
    """The shortlist obfuscates surnames. `Zh***g` cannot match anybody.

    Before this guard the match key was built from first plus last, both
    non-empty, so an approval was parked and Allow spent one real credit on a
    lookup that could not succeed.
    """
    http = FakeHttp({MATCH_URL: APOLLO_PERSON})
    app = _app(tmp_path, http)
    client = TestClient(app)
    person_id, session_id = _person(app, apollo_id=None, first="Fisher", last="Zh***g")

    res = _park(client, person_id, session_id)

    assert res.status_code == 409
    assert res.json()["code"] == "no_match_key"
    assert app.state.inbox.pending() == []
    assert _match_calls(http) == []


def test_a_kept_candidate_enriches_through_its_stored_apollo_id(tmp_path):
    """Keep stores the Apollo ID. A masked surname must not strand it."""
    http = FakeHttp({MATCH_URL: APOLLO_PERSON})
    app = _app(tmp_path, http)
    client = TestClient(app)
    person_id, session_id = _person(
        app, apollo_id="679c7e37", first="Fisher", last="Zh***g"
    )

    parked = _park(client, person_id, session_id)

    assert parked.status_code == 201, parked.text
    resource = parked.json()["item"]["resource"]
    assert resource["matched_on"] == "apollo_id"
    assert "1 Apollo credit" in resource["reason"]
    # Parking still spends nothing.
    assert _match_calls(http) == []

    decided = _decide(client, parked.json()["item"]["id"])

    assert decided.status_code == 200, decided.text
    calls = _match_calls(http)
    assert len(calls) == 1
    # The masked surname must not travel to Apollo at all.
    body = calls[0]["json"]
    assert body.get("id") == "679c7e37"
    assert body.get("last_name") is None
    assert app.state.people.get(person_id)["email"] == "ada@analytic.example"


def test_a_real_surname_still_matches_on_name(tmp_path):
    """Control. The mask guard must not swallow ordinary surnames."""
    http = FakeHttp({MATCH_URL: APOLLO_PERSON})
    app = _app(tmp_path, http)
    client = TestClient(app)
    person_id, session_id = _person(
        app, apollo_id="ada", first="Ada", last="Lovelace"
    )

    parked = _park(client, person_id, session_id)

    assert parked.status_code == 201, parked.text
    assert parked.json()["item"]["resource"]["matched_on"] == "name"
