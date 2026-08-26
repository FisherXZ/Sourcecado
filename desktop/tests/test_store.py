import sqlite3

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


def test_presentation_events_round_trip_in_a_separate_append_only_log(tmp_path):
    store = ConversationStore(tmp_path)
    model_message = {"role": "user", "content": "hi"}
    event = {
        "version": 2,
        "type": "turn_start",
        "session_id": "main",
        "run_id": "run-1",
        "event_id": "event-1",
        "message_id": "message-1",
        "part_id": "part-1",
        "state": "running",
    }

    store.append("main", model_message)
    store.append_event("main", event)

    assert store.load("main") == [model_message]
    assert store.load_events("main") == [event]
    assert not any("version" in message for message in store.load("main"))
    assert (tmp_path / "events" / "main.jsonl").is_file()


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


def test_open_session_moves_it_to_front_without_rewriting_messages(tmp_path):
    store = ConversationStore(tmp_path)
    store.append("a", {"role": "user", "content": "first chat"})
    store.append("b", {"role": "user", "content": "second chat"})

    store.set_open_session("a")

    assert [row["session_id"] for row in store.list_sessions()] == ["a", "b"]
    assert store.load("a") == [{"role": "user", "content": "first chat"}]


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


def test_pin_session_survives_store_reopen(tmp_path):
    store = ConversationStore(tmp_path)
    store.create_session("s1")

    store.set_session_pinned("s1", True)

    reopened = ConversationStore(tmp_path)
    assert reopened.index("s1")["pinned"] is True


def test_list_sessions_returns_boolean_pin_metadata(tmp_path):
    store = ConversationStore(tmp_path)
    store.create_session("s1")
    store.set_session_pinned("s1", True)

    assert store.list_sessions()[0]["pinned"] is True


def test_existing_sessions_table_migrates_pin_state(tmp_path):
    db = sqlite3.connect(tmp_path / "club.db")
    db.execute(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            title TEXT,
            n_msgs INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.execute(
        "INSERT INTO sessions (session_id, title, n_msgs) VALUES ('legacy', 'Legacy chat', 2)"
    )
    db.commit()
    db.close()

    store = ConversationStore(tmp_path)

    assert store.index("legacy")["pinned"] is False


def test_list_sessions_hides_scheduler_runs(tmp_path):
    store = ConversationStore(tmp_path)
    store.append("chat", {"role": "user", "content": "hello"})
    store.create_session("sched-1")
    ids = [row["session_id"] for row in store.list_sessions()]
    assert ids == ["chat"]
    assert store.load("sched-1") == []


def test_queue_add_is_idempotent_and_survives_store_reopen(tmp_path):
    store = ConversationStore(tmp_path)
    store.create_session("thread-alpha")
    apply_command = getattr(store, "apply_queue_command", None)
    assert callable(apply_command), "ConversationStore must expose queue commands"

    command = {
        "type": "queue_add",
        "command_id": "command-add-1",
        "item_id": "queue-item-1",
        "text": "Find five candidates",
    }
    first = apply_command("thread-alpha", command)
    duplicate = apply_command("thread-alpha", command)

    assert first == duplicate
    assert first["status"] == "accepted"
    assert first["items"] == [
        {
            "id": "queue-item-1",
            "session_id": "thread-alpha",
            "text": "Find five candidates",
            "position": 0,
            "state": "waiting",
            "error": None,
            "created_at": first["items"][0]["created_at"],
            "updated_at": first["items"][0]["updated_at"],
        }
    ]
    reopened = ConversationStore(tmp_path)
    assert reopened.list_queue("thread-alpha") == first["items"]


def test_store_reopen_interrupts_orphaned_sending_items_and_pauses_only_affected_threads(
    tmp_path,
):
    store = ConversationStore(tmp_path)
    for sid in ("thread-alpha", "thread-beta", "thread-ready"):
        store.create_session(sid)
    fixtures = {
        "thread-alpha": ("alpha-first", "alpha queued first"),
        "thread-beta": ("beta-first", "beta queued first"),
        "thread-ready": ("ready-first", "ready queued first"),
    }
    for sid, (item_id, text) in fixtures.items():
        store.apply_queue_command(
            sid,
            {
                "type": "queue_add",
                "command_id": f"add-{sid}",
                "item_id": item_id,
                "text": text,
            },
        )
    store.apply_queue_command(
        "thread-alpha",
        {
            "type": "queue_add",
            "command_id": "add-alpha-second",
            "item_id": "alpha-second",
            "text": "alpha queued second",
        },
    )
    assert store.claim_next_queue("thread-alpha")["id"] == "alpha-first"
    assert store.claim_next_queue("thread-beta")["id"] == "beta-first"

    reopened = ConversationStore(tmp_path)

    alpha = reopened.list_queue("thread-alpha")
    beta = reopened.list_queue("thread-beta")
    ready = reopened.list_queue("thread-ready")
    assert [(item["id"], item["text"], item["position"]) for item in alpha] == [
        ("alpha-first", "alpha queued first", 0),
        ("alpha-second", "alpha queued second", 1),
    ]
    assert alpha[0]["state"] == "interrupted"
    assert "restarted" in alpha[0]["error"].lower()
    assert alpha[1]["state"] == "waiting"
    assert beta[0]["state"] == "interrupted"
    assert ready[0]["state"] == "waiting"
    assert reopened.queue_paused("thread-alpha") is True
    assert reopened.queue_paused("thread-beta") is True
    assert reopened.queue_paused("thread-ready") is False
    assert reopened.claim_next_queue("thread-alpha") is None
    assert reopened.claim_next_queue("thread-beta") is None
    assert reopened.claim_next_queue("thread-ready")["id"] == "ready-first"


def test_reconciled_queue_requires_idempotent_retry_and_resume_before_advancing(
    tmp_path,
):
    store = ConversationStore(tmp_path)
    store.create_session("thread-alpha")
    for index, text in enumerate(("first prompt", "second prompt"), start=1):
        store.apply_queue_command(
            "thread-alpha",
            {
                "type": "queue_add",
                "command_id": f"add-{index}",
                "item_id": f"item-{index}",
                "text": text,
            },
        )
    assert store.claim_next_queue("thread-alpha")["id"] == "item-1"
    reopened = ConversationStore(tmp_path)

    retry = {
        "type": "queue_retry",
        "command_id": "retry-interrupted-1",
        "item_id": "item-1",
    }
    first_retry = reopened.apply_queue_command("thread-alpha", retry)
    duplicate_retry = reopened.apply_queue_command("thread-alpha", retry)
    assert first_retry == duplicate_retry
    assert first_retry["status"] == "accepted"
    assert first_retry["paused"] is True
    assert first_retry["items"][0]["state"] == "waiting"
    assert reopened.claim_next_queue("thread-alpha") is None

    resume = {"type": "queue_resume", "command_id": "resume-interrupted-1"}
    first_resume = reopened.apply_queue_command("thread-alpha", resume)
    duplicate_resume = reopened.apply_queue_command("thread-alpha", resume)
    assert first_resume == duplicate_resume
    assert first_resume["status"] == "accepted"
    assert first_resume["paused"] is False

    claimed = reopened.claim_next_queue("thread-alpha")
    assert claimed is not None
    assert claimed["id"] == "item-1"
    assert claimed["text"] == "first prompt"
    assert reopened.claim_next_queue("thread-alpha") is None
    reopened.finish_queue_item("thread-alpha", "item-1", state="complete")
    advanced = reopened.claim_next_queue("thread-alpha")
    assert advanced is not None
    assert advanced["id"] == "item-2"
    assert advanced["text"] == "second prompt"


def test_queue_edit_move_and_remove_are_ordered_and_thread_isolated(tmp_path):
    store = ConversationStore(tmp_path)
    for sid in ("thread-alpha", "thread-beta"):
        store.create_session(sid)
    for index, text in enumerate(("first", "second", "third"), start=1):
        store.apply_queue_command(
            "thread-alpha",
            {
                "type": "queue_add",
                "command_id": f"add-{index}",
                "item_id": f"item-{index}",
                "text": text,
            },
        )
    store.apply_queue_command(
        "thread-beta",
        {
            "type": "queue_add",
            "command_id": "beta-add",
            "item_id": "beta-item",
            "text": "beta only",
        },
    )

    edited = store.apply_queue_command(
        "thread-alpha",
        {
            "type": "queue_edit",
            "command_id": "edit-2",
            "item_id": "item-2",
            "text": "second revised",
        },
    )
    moved = store.apply_queue_command(
        "thread-alpha",
        {
            "type": "queue_move",
            "command_id": "move-3-first",
            "item_id": "item-3",
            "before_id": "item-1",
        },
    )
    removed = store.apply_queue_command(
        "thread-alpha",
        {
            "type": "queue_remove",
            "command_id": "remove-1",
            "item_id": "item-1",
        },
    )

    assert edited["status"] == "accepted"
    assert moved["status"] == "accepted"
    assert removed["status"] == "accepted"
    assert [(item["id"], item["text"], item["position"]) for item in removed["items"]] == [
        ("item-3", "third", 0),
        ("item-2", "second revised", 1),
    ]
    assert [item["id"] for item in store.list_queue("thread-beta")] == ["beta-item"]


def test_queue_pause_requires_explicit_resume_and_claims_only_one_item(tmp_path):
    store = ConversationStore(tmp_path)
    store.create_session("thread-alpha")
    for index in (1, 2):
        store.apply_queue_command(
            "thread-alpha",
            {
                "type": "queue_add",
                "command_id": f"pause-add-{index}",
                "item_id": f"pause-item-{index}",
                "text": f"queued {index}",
            },
        )

    set_paused = getattr(store, "set_queue_paused", None)
    claim_next = getattr(store, "claim_next_queue", None)
    assert callable(set_paused) and callable(claim_next)
    set_paused("thread-alpha", True)
    assert claim_next("thread-alpha") is None

    resumed = store.apply_queue_command(
        "thread-alpha",
        {
            "type": "queue_resume",
            "command_id": "resume-command",
        },
    )
    claimed = claim_next("thread-alpha")

    assert resumed["status"] == "accepted"
    assert resumed["paused"] is False
    assert claimed is not None
    assert claimed["id"] == "pause-item-1"
    assert claimed["state"] == "sending"
    assert claim_next("thread-alpha") is None
    reopened = ConversationStore(tmp_path)
    assert reopened.queue_paused("thread-alpha") is True
    assert reopened.list_queue("thread-alpha")[0]["state"] == "interrupted"


def test_queue_failure_retains_text_and_retry_returns_it_to_waiting(tmp_path):
    store = ConversationStore(tmp_path)
    store.create_session("thread-alpha")
    store.apply_queue_command(
        "thread-alpha",
        {
            "type": "queue_add",
            "command_id": "failed-add",
            "item_id": "failed-item",
            "text": "Retain this prompt",
        },
    )
    assert store.claim_next_queue("thread-alpha")["id"] == "failed-item"
    finish_item = getattr(store, "finish_queue_item", None)
    assert callable(finish_item)
    finish_item(
        "thread-alpha", "failed-item", state="failed", error="provider offline"
    )

    failed = store.list_queue("thread-alpha")[0]
    assert failed["text"] == "Retain this prompt"
    assert failed["state"] == "failed"
    assert failed["error"] == "provider offline"

    retried = store.apply_queue_command(
        "thread-alpha",
        {
            "type": "queue_retry",
            "command_id": "retry-failed-item",
            "item_id": "failed-item",
        },
    )
    assert retried["status"] == "accepted"
    assert retried["items"][0]["state"] == "waiting"
    assert retried["items"][0]["error"] is None
