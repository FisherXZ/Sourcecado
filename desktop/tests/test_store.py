import pytest

from coworker.store import ConversationStore, valid_session_id


def test_append_roundtrip_jsonl(tmp_path):
    store = ConversationStore(tmp_path)
    store.append("main", {"role": "user", "content": "hi"})
    store.append("main", {"role": "assistant", "content": "hello"})
    messages = store.load("main")
    assert messages == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    path = tmp_path / "conversations" / "main.jsonl"
    assert path.is_file()
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2


def test_sqlite_index_tracks_title_and_count(tmp_path):
    store = ConversationStore(tmp_path)
    store.append("main", {"role": "user", "content": "what time is it?"})
    store.append("main", {"role": "assistant", "content": "noon"})
    row = store.index("main")
    assert row is not None
    assert row["session_id"] == "main"
    assert row["title"] == "what time is it?"
    assert row["n_msgs"] == 2
    assert (tmp_path / "club.db").is_file()


def test_replace_all_rewrites_jsonl(tmp_path):
    store = ConversationStore(tmp_path)
    store.append("main", {"role": "user", "content": "hi"})
    store.append("main", {"role": "assistant", "content": "hello"})
    store.replace_all(
        "main",
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "tool", "tool_call_id": "call_1", "name": "now", "content": "{}"},
        ],
    )
    messages = store.load("main")
    assert len(messages) == 3
    assert messages[2]["tool_call_id"] == "call_1"
    assert store.index("main")["n_msgs"] == 3


def test_load_missing_is_empty(tmp_path):
    store = ConversationStore(tmp_path)
    assert store.load("main") == []
    assert store.index("main") is None


def test_first_user_line_becomes_title(tmp_path):
    store = ConversationStore(tmp_path)
    store.append("main", {"role": "user", "content": "send a test message saying hello"})
    store.append("main", {"role": "user", "content": "later"})
    assert store.index("main")["title"] == "send a test message saying hello"


def test_list_sessions_newest_first(tmp_path):
    store = ConversationStore(tmp_path)
    store.append("a", {"role": "user", "content": "first chat"})
    store.append("b", {"role": "user", "content": "second chat"})
    ids = [row["session_id"] for row in store.list_sessions()]
    assert ids == ["b", "a"]
    assert store.list_sessions()[0]["title"] == "second chat"


def test_session_id_rejects_path_escape(tmp_path):
    store = ConversationStore(tmp_path)
    assert valid_session_id("main")
    assert valid_session_id("sched-1")
    assert not valid_session_id("../secrets")
    assert not valid_session_id("foo/bar")
    with pytest.raises(ValueError, match="invalid session id"):
        store.append("../secrets", {"role": "user", "content": "nope"})
    assert not (tmp_path / "secrets.jsonl").exists()
    assert list((tmp_path / "conversations").iterdir()) == []


def test_create_session_is_empty_and_open(tmp_path):
    store = ConversationStore(tmp_path)
    row = store.create_session()
    assert row["n_msgs"] == 0
    assert row["title"] is None
    assert store.load(row["session_id"]) == []
    store.set_open_session(row["session_id"])
    assert store.open_session_id() == row["session_id"]


def test_rename_survives_later_append(tmp_path):
    store = ConversationStore(tmp_path)
    store.append("s1", {"role": "user", "content": "original title here"})
    store.rename_session("s1", "Alyssa outreach")
    store.append("s1", {"role": "user", "content": "later"})
    assert store.index("s1")["title"] == "Alyssa outreach"


def test_list_sessions_hides_scheduler_runs(tmp_path):
    store = ConversationStore(tmp_path)
    store.append("chat", {"role": "user", "content": "hello"})
    store.create_session("sched-1")
    ids = [row["session_id"] for row in store.list_sessions()]
    assert ids == ["chat"]
    assert store.load("sched-1") == []
