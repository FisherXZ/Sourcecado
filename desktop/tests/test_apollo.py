import os

import pytest

from coworker.apollo import MATCH_URL, SEARCH_URL, FakeHttp, LiveHttp, search_people
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


def test_apollo_enrich_returns_contact():
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
    ok, result = execute(
        "apollo_enrich_contact",
        {"firstName": "Tim", "lastName": "Zheng", "organizationName": "Apollo"},
        http=http,
        apollo_key="test-key",
    )
    assert ok is True
    assert result["name"] == "Tim Zheng"
    assert result["email"] == "tim@apollo.io"
    assert result["organizationName"] == "Apollo.io"


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
