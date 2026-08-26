from coworker.apollo import FakeHttp
from coworker.people import PersonStore
from coworker.tools import execute
from coworker.web import SEARCH_URL


def test_web_search_returns_title_and_url():
    http = FakeHttp(
        {
            SEARCH_URL: {
                "results": [
                    {
                        "title": "Ada Lovelace",
                        "url": "https://example.com/ada",
                        "content": "mathematician",
                    },
                    {
                        "title": "Analytic",
                        "url": "https://example.com/analytic",
                        "content": "engines",
                    },
                ]
            }
        }
    )
    ok, result = execute(
        "web_search",
        {"query": "Ada Lovelace"},
        http=http,
        tavily_key="tvly-test",
    )
    assert ok is True
    assert result["results"][0]["title"] == "Ada Lovelace"
    assert result["results"][0]["url"] == "https://example.com/ada"
    assert http.calls[0]["headers"]["Authorization"] == "Bearer tvly-test"


def test_web_search_missing_key_is_clear_error():
    ok, result = execute("web_search", {"query": "Ada"})
    assert ok is False
    assert "TAVILY_API_KEY" in result["error"]


def test_bound_web_search_files_web_source(tmp_path):
    import asyncio

    from coworker.inbox import Inbox
    from coworker.provider import FakeProvider, ToolCall
    from coworker.store import ConversationStore
    from coworker.tools import OPENAI_TOOLS
    from coworker.turn import run_turn

    people = PersonStore(tmp_path)
    person = people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    people.bind_session("sess-ada", person["person_id"])
    conv = ConversationStore(tmp_path)
    http = FakeHttp(
        {
            SEARCH_URL: {
                "results": [
                    {"title": "Ada", "url": "https://example.com/ada", "content": "bio"}
                ]
            }
        }
    )
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(id="w1", name="web_search", arguments={"query": "Ada"})
                ]
            },
            {"deltas": ("Found a page.",)},
        ]
    )
    asyncio.run(
        run_turn(
            text="search the web",
            sid="sess-ada",
            store=conv,
            provider=fake,
            persona=None,
            skills=None,
            inbox=Inbox(conv),
            openai_tools=OPENAI_TOOLS,
            execute_kwargs={"people": people, "http": http, "tavily_key": "tvly-test"},
        )
    )
    timeline = people.timeline(person["person_id"])
    assert timeline[0]["source"] == "web"
    assert timeline[0]["kind"] == "search"
    assert timeline[0]["payload"]["urls"] == ["https://example.com/ada"]
