from coworker.permissions import AUTO, ASK, decide
from coworker.tools import OPENAI_TOOLS


def test_openai_tools_are_in_auto_or_ask():
    names = [schema["function"]["name"] for schema in OPENAI_TOOLS]
    for name in names:
        assert name in AUTO or name in ASK
    assert AUTO.isdisjoint(ASK)


def test_now_auto_allows():
    d = decide("now")
    assert d.allowed is True
    assert d.needs_user is False


def test_memory_tools_auto_allow():
    for name in ("remember", "memory_update", "memory_forget", "load_skill"):
        d = decide(name)
        assert d.allowed is True
        assert d.needs_user is False


def test_send_test_is_unknown():
    d = decide("send_test")
    assert d.allowed is False
    assert d.needs_user is False


def test_gmail_draft_asks():
    d = decide("gmail_draft")
    assert d.allowed is False
    assert d.needs_user is True


def test_gmail_search_read_auto():
    assert decide("gmail_search").needs_user is False
    assert decide("gmail_read").needs_user is False


def test_mcp_write_tools_are_denied():
    d = decide("mcp__granola__create_note")
    assert d.allowed is False
    assert d.needs_user is False
    assert decide("mcp__granola__list_meetings").allowed is True


def test_apollo_search_auto_enrich_asks():
    search = decide("apollo_search_people")
    assert search.allowed is True
    assert search.needs_user is False
    enrich = decide("apollo_enrich_contact")
    assert enrich.allowed is False
    assert enrich.needs_user is True
