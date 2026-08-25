from coworker.store import ConversationStore
from coworker.tools import execute, now


def test_now_returns_la_clock():
    ok, result = execute("now", {})
    assert ok is True
    assert result["tz"] == "America/Los_Angeles"
    assert "T" in result["iso"]
    assert result["weekday"] in {
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    }
    assert now()["tz"] == "America/Los_Angeles"


def test_unknown_tool():
    ok, result = execute("send_email", {})
    assert ok is False
    assert "unknown" in result["error"]


def test_remember_update_forget_via_execute(tmp_path):
    store = ConversationStore(tmp_path)
    ok, saved = execute("remember", {"content": "prefers dark mode"}, store=store)
    assert ok is True
    assert saved["saved"] is True
    assert saved["id"] == 1
    ok, updated = execute(
        "memory_update",
        {"memory_id": 1, "content": "prefers dark mode on Club"},
        store=store,
    )
    assert ok is True
    assert updated["updated"] is True
    ok, forgotten = execute("memory_forget", {"memory_id": 1}, store=store)
    assert ok is True
    assert forgotten["forgotten"] is True
    assert store.list_memories() == []


def test_remember_requires_content(tmp_path):
    store = ConversationStore(tmp_path)
    ok, result = execute("remember", {}, store=store)
    assert ok is False
    assert "content" in result["error"]
