import os

import pytest

from coworker.apollo import (
    MATCH_URL,
    SEARCH_URL,
    FakeHttp,
    LiveHttp,
    enrichment_match,
    enrichment_resource,
    apollo_evidence,
    search_people,
)
from coworker.people import PersonStore
from coworker.evidence_envelope import model_payload
from coworker.tools import execute


def test_apollo_search_does_not_invent_emails():
    http = FakeHttp(
        {
            SEARCH_URL: {
                "people": [
                    {
                        "id": "55d725d1f3e5bb49f90024ff",
                        "first_name": "Tim",
                        "last_name_obfuscated": "Do***e",
                        "title": "CEO",
                        "has_email": True,
                        "has_direct_phone": "Yes",
                        "organization": {"name": "Apollo"},
                        "email": "should-not-leak@apollo.io",
                    }
                ]
            }
        }
    )
    ok, result = execute(
        "apollo_search_people",
        {"organizationName": "Apollo", "personTitles": ["CEO"]},
        http=http,
        apollo_key="test-key",
    )
    assert ok is True
    person = result["people"][0]
    assert person["firstName"] == "Tim"
    assert person["lastNameObfuscated"] == "Do***e"
    assert person["hasEmail"] is True
    assert "email" not in person
    assert "should-not-leak" not in str(result)
    assert http.calls[0]["headers"]["x-api-key"] == "test-key"


def test_apollo_search_model_evidence_never_contains_the_masked_surname():
    parts = apollo_evidence(
        "apollo_search_people",
        {
            "people": [
                {
                    "apolloId": "person-fisher",
                    "firstName": "Fisher",
                    "lastNameObfuscated": "Zh***g",
                    "title": "Building GTM AI",
                    "organizationName": "The Hog",
                }
            ]
        },
    )

    rendered = str(model_payload(parts))

    assert "Fisher" in rendered
    assert "surname hidden by Apollo" in rendered
    assert "Zh***g" not in rendered


@pytest.mark.parametrize("masked_name", ["Ada L***e", "Ada 张***李"])
def test_apollo_enrichment_model_evidence_never_contains_a_masked_name(masked_name):
    parts = apollo_evidence(
        "apollo_enrich_contact",
        {
            "name": masked_name,
            "title": "Founder",
            "organizationName": "Analytic",
            "email": "ada@analytic.example",
        },
    )

    rendered = str(model_payload(parts))

    assert "Ada (surname hidden by Apollo)" in rendered
    assert masked_name not in rendered


def test_apollo_enrich_returns_contact(tmp_path):
    http = FakeHttp(
        {
            MATCH_URL: {
                "person": {
                    "name": "Tim Zheng",
                    "title": "CEO",
                    "organization": {"name": "Apollo.io"},
                    "linkedin_url": "https://www.linkedin.com/in/timzheng",
                    "email": "tim@apollo.io",
                    "phone_numbers": [{"raw_number": "+1 555"}],
                }
            }
        }
    )
    people = PersonStore(tmp_path)
    person = people.keep_from_apollo(
        apollo_id="tim",
        first_name="Tim",
        last_name_obfuscated="Z",
        title="CEO",
        company="Apollo",
    )
    people.bind_session("sess-tim", person["person_id"])
    ok, result = execute(
        "apollo_enrich_contact",
        {"firstName": "Tim", "lastName": "Zheng", "organizationName": "Apollo"},
        http=http,
        apollo_key="test-key",
        people=people,
        session_id="sess-tim",
    )
    assert ok is True
    assert result["name"] == "Tim Zheng"
    assert result["email"] == "tim@apollo.io"
    assert result["organizationName"] == "Apollo.io"
    assert people.get(person["person_id"])["email"] == "tim@apollo.io"


def test_apollo_missing_key_fails_clearly():
    ok, result = execute("apollo_search_people", {"organizationName": "Apollo"})
    assert ok is False
    assert "APOLLO_API_KEY" in result["error"]
    ok, result = execute(
        "apollo_enrich_contact",
        {"firstName": "Tim", "lastName": "Zheng"},
        http=FakeHttp(),
    )
    assert ok is False
    assert "APOLLO_API_KEY" in result["error"]


@pytest.mark.skipif(not os.environ.get("CLUB_RUN_LIVE_SMOKE"), reason="live apollo")
def test_live_apollo_search_returns_no_emails():
    key = os.environ["APOLLO_API_KEY"]
    out = search_people(http=LiveHttp(), api_key=key, organization_name="Abridge", limit=3)
    assert "people" in out
    for person in out["people"]:
        assert "email" not in person or person.get("email") in (None, "")


def test_a_masked_surname_falls_back_to_the_person_files_apollo_id(tmp_path):
    """Issue #126: the shortlist hands the model an obfuscated surname.

    The model must not spend a credit guessing at it, and must not be
    stranded either. The person file already stores the exact Apollo ID.
    """
    http = FakeHttp(
        {
            MATCH_URL: {
                "person": {
                    "id": "679c7e37",
                    "name": "Fisher Zhang",
                    "title": "Building GTM AI",
                    "organization": {"name": "The Hog"},
                    "email": "fisher@thehog.example",
                    "phone_numbers": [],
                }
            }
        }
    )
    people = PersonStore(tmp_path)
    person = people.keep_from_apollo(
        apollo_id="679c7e37",
        first_name="Fisher",
        last_name_obfuscated="Zh***g",
        title="Building GTM AI",
        company="The Hog",
    )
    people.bind_session("sess-hog", person["person_id"])

    ok, result = execute(
        "apollo_enrich_contact",
        # Exactly what the shortlist gives the model.
        {"firstName": "Fisher", "lastName": "Zh***g", "organizationName": "The Hog"},
        http=http,
        apollo_key="test-key",
        people=people,
        session_id="sess-hog",
    )

    assert ok is True, result
    body = [c for c in http.calls if c["url"] == MATCH_URL][0]["json"]
    assert body["id"] == "679c7e37"
    # The mask must never travel to Apollo.
    assert body["last_name"] is None
    assert people.get(person["person_id"])["email"] == "fisher@thehog.example"


def test_enrichment_approval_names_an_incomplete_person_without_the_mask(tmp_path):
    people = PersonStore(tmp_path)
    person = people.keep_from_apollo(
        apollo_id="679c7e37",
        first_name="Fisher",
        last_name_obfuscated="Zh***g",
        title="Building GTM AI",
        company="The Hog",
    )
    match = enrichment_match(person)
    assert match is not None

    resource = enrichment_resource(person, match)

    assert resource["person"] == "Fisher (surname hidden by Apollo)"
    assert "Zh***g" not in str(resource)


def test_a_masked_surname_with_no_apollo_id_refuses_rather_than_spending(tmp_path):
    http = FakeHttp({MATCH_URL: {"person": {"id": "x", "name": "Nobody"}}})
    people = PersonStore(tmp_path)
    person = people.keep_from_apollo(
        apollo_id=None,
        first_name="Fisher",
        last_name_obfuscated="Zh***g",
        title=None,
        company=None,
    )
    people.bind_session("sess-none", person["person_id"])

    ok, result = execute(
        "apollo_enrich_contact",
        {"firstName": "Fisher", "lastName": "Zh***g"},
        http=http,
        apollo_key="test-key",
        people=people,
        session_id="sess-none",
    )

    assert ok is False
    assert "obfuscated" in result["error"]
    assert [c for c in http.calls if c["url"] == MATCH_URL] == []
