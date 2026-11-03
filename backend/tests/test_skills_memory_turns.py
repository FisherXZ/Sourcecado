"""Skills and memory the director manages in the app, proven through real turns.

Each test drives the websocket turn loop with a scripted model and reads what
the model was actually sent: the system prompt for the skill catalog and the
saved memory, and the tool results for `load_skill` and the memory tools.
"""

from itertools import count

from fastapi.testclient import TestClient

from coworker.provider import FakeProvider, ToolCall
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-skills-memory"
HEADERS = {TOKEN_HEADER: TOKEN}


def _client(tmp_path, provider: FakeProvider) -> TestClient:
    return TestClient(create_app(token=TOKEN, provider=provider, state=tmp_path))


def _turn(client: TestClient, text: str) -> list[dict]:
    """Run one turn and return its events.

    A new connection first replays the previous turn's end, so the turn's own
    events start at its `turn_start`.
    """
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": text})
        events: list[dict] = []
        while True:
            event = ws.receive_json()
            if event["type"] == "turn_start":
                events = []
            events.append(event)
            started = events[0]["type"] == "turn_start"
            if event["type"] == "error" or (started and event["type"] == "turn_end"):
                return events


def _finished(events: list[dict], name: str) -> dict:
    return next(e for e in events if e["type"] == "tool_finished" and e["name"] == name)


_ids = count(1)


def _call(tool: str, **arguments) -> dict:
    """One scripted tool call. Ids are unique: the loop replays a repeated id."""
    call_id = f"call_{tool}_{next(_ids)}"
    return {"tool_calls": [ToolCall(id=call_id, name=tool, arguments=arguments)]}


INTRO = {
    "name": "intro-email",
    "purpose": "Draft a warm first email to one Person.",
    "use_when": "The director asks for a first email.",
    "instructions": "Open with the shared Codeology link. Keep it under 120 words.",
}


# --- skills -----------------------------------------------------------------


def test_a_saved_skill_is_offered_to_the_model_and_loads_in_the_turn(tmp_path):
    model = FakeProvider(
        steps=[_call("load_skill", name="intro-email"), {"deltas": ("Drafting.",)}]
    )
    client = _client(tmp_path, model)

    created = client.post("/v1/skills", json=INTRO, headers=HEADERS)
    assert created.status_code == 200
    assert created.json()["skill"]["source"] == "workspace"
    names = [row["name"] for row in client.get("/v1/skills", headers=HEADERS).json()["skills"]]
    assert "intro-email" in names

    events = _turn(client, "/intro-email for Ada")

    system = model.calls[0][0]["content"]
    assert "- intro-email: Draft a warm first email to one Person." in system
    assert "starts with /name asks for that skill" in system
    loaded = _finished(events, "load_skill")
    assert loaded["ok"] is True
    assert loaded["result"]["instructions"] == INTRO["instructions"]
    tool_message = next(m for m in model.calls[1] if m["role"] == "tool")
    assert "Open with the shared Codeology link." in tool_message["content"]


def test_an_edited_skill_loads_its_new_instructions_in_the_next_turn(tmp_path):
    model = FakeProvider(
        steps=[
            _call("load_skill", name="intro-email"),
            {"deltas": ("First.",)},
            _call("load_skill", name="intro-email"),
            {"deltas": ("Second.",)},
        ]
    )
    client = _client(tmp_path, model)
    client.post("/v1/skills", json=INTRO, headers=HEADERS)
    first = _finished(_turn(client, "/intro-email"), "load_skill")

    edited = client.put(
        "/v1/skills/intro-email",
        json={**INTRO, "instructions": "Lead with their latest launch."},
        headers=HEADERS,
    )
    assert edited.status_code == 200
    second = _finished(_turn(client, "/intro-email"), "load_skill")

    assert first["result"]["instructions"].startswith("Open with")
    assert second["result"]["instructions"] == "Lead with their latest launch."
    on_disk = (tmp_path / "skills" / "intro-email" / "SKILL.md").read_text()
    assert "Lead with their latest launch." in on_disk
    assert "Open with" not in on_disk


def test_editing_a_builtin_saves_a_workspace_copy_and_deleting_it_restores_the_builtin(
    tmp_path,
):
    model = FakeProvider(
        steps=[_call("load_skill", name="weekly-sourcing"), {"deltas": ("Ok.",)}]
    )
    client = _client(tmp_path, model)

    saved = client.put(
        "/v1/skills/weekly-sourcing",
        json={
            "purpose": "Shortlist who to work this week.",
            "use_when": "",
            "instructions": "Only Berkeley alumni this week.",
        },
        headers=HEADERS,
    ).json()["skill"]
    assert saved["source"] == "workspace"
    assert saved["builtin_default"] is True
    loaded = _finished(_turn(client, "/weekly-sourcing"), "load_skill")
    assert loaded["result"]["instructions"] == "Only Berkeley alumni this week."
    assert "- weekly-sourcing: Shortlist who to work this week." in model.calls[0][0]["content"]

    assert client.delete("/v1/skills/weekly-sourcing", headers=HEADERS).status_code == 200
    restored = next(
        row
        for row in client.get("/v1/skills", headers=HEADERS).json()["skills"]
        if row["name"] == "weekly-sourcing"
    )
    assert restored["source"] == "builtin"
    assert "Only Berkeley alumni" not in restored["instructions"]
    builtin_delete = client.delete("/v1/skills/weekly-sourcing", headers=HEADERS)
    assert builtin_delete.status_code == 400


def test_a_deleted_skill_leaves_the_catalog_and_cannot_be_loaded(tmp_path):
    model = FakeProvider(
        steps=[_call("load_skill", name="intro-email"), {"deltas": ("Gone.",)}]
    )
    client = _client(tmp_path, model)
    client.post("/v1/skills", json=INTRO, headers=HEADERS)

    assert client.delete("/v1/skills/intro-email", headers=HEADERS).status_code == 200
    events = _turn(client, "/intro-email")

    assert "intro-email" not in model.calls[0][0]["content"]
    loaded = _finished(events, "load_skill")
    assert loaded["ok"] is False
    assert not (tmp_path / "skills" / "intro-email").exists()


def test_skill_saves_reject_what_cannot_be_saved(tmp_path):
    client = _client(tmp_path, FakeProvider())

    def post(**changes):
        return client.post("/v1/skills", json={**INTRO, **changes}, headers=HEADERS)

    assert post(name="Intro Email").status_code == 400
    assert post(name="../escape").status_code == 400
    assert post(purpose=" ").status_code == 400
    assert post(instructions="").status_code == 400
    assert post(instructions="Read [REDACTED PATH] first.").status_code == 400
    assert post().status_code == 200
    assert post().status_code == 409
    assert post(name="weekly-sourcing").status_code == 409
    missing = client.put("/v1/skills/nobody", json=INTRO, headers=HEADERS)
    assert missing.status_code == 404
    assert client.delete("/v1/skills/nobody", headers=HEADERS).status_code == 404
    assert not (tmp_path.parent / "escape").exists()


# --- memory -----------------------------------------------------------------


def test_a_memory_the_director_writes_is_in_the_next_prompt_with_its_source(tmp_path):
    model = FakeProvider(deltas=("Under 120 words.",))
    client = _client(tmp_path, model)

    created = client.post(
        "/v1/memory", json={"content": "Keep first emails under 120 words."}, headers=HEADERS
    )
    assert created.status_code == 200
    memory = created.json()["memory"]
    assert memory["in_use"] is True

    _turn(client, "how long should the email be?")

    system = model.calls[0][0]["content"]
    assert (
        f"[#{memory['id']}] Keep first emails under 120 words. "
        f"(sourcecado:memory/{memory['id']}, current)"
    ) in system


def test_a_memory_the_agent_saves_waits_for_keep_then_reaches_the_prompt(tmp_path):
    model = FakeProvider(
        steps=[
            _call("remember", content="Fisher prefers Tuesday sends."),
            {"deltas": ("Saved. Keep it on the Memory page to use it later.",)},
            {"deltas": ("Noted.",)},
        ]
    )
    client = _client(tmp_path, model)

    events = _turn(client, "remember that I prefer Tuesday sends")
    saved = _finished(events, "remember")
    assert saved["ok"] is True
    assert saved["result"]["in_use"] is False
    assert "Memory page" in saved["result"]["next"]
    listed = client.get("/v1/memory", headers=HEADERS).json()
    assert [(item["content"], item["in_use"]) for item in listed["items"]] == [
        ("Fisher prefers Tuesday sends.", False)
    ]

    _turn(client, "when do I send?")
    assert "Tuesday sends" not in model.calls[-1][0]["content"]

    memory_id = saved["result"]["id"]
    kept = client.post(
        f"/v1/memory/{memory_id}/classification",
        json={"category": "operator_preference"},
        headers=HEADERS,
    )
    assert kept.status_code == 200
    _turn(client, "when do I send?")
    assert (
        f"[#{memory_id}] Fisher prefers Tuesday sends. (sourcecado:memory/{memory_id}, current)"
        in model.calls[-1][0]["content"]
    )


def test_a_director_edit_stays_in_use_and_the_prompt_shows_only_the_new_text(tmp_path):
    model = FakeProvider(deltas=("Ok.",))
    client = _client(tmp_path, model)
    memory_id = client.post(
        "/v1/memory", json={"content": "Sign emails as Fisher."}, headers=HEADERS
    ).json()["memory"]["id"]

    edited = client.patch(
        f"/v1/memory/{memory_id}",
        json={"content": "Sign emails as Fisher X."},
        headers=HEADERS,
    )
    assert edited.status_code == 200
    assert edited.json()["memory"]["in_use"] is True
    _turn(client, "sign it")

    system = model.calls[0][0]["content"]
    assert f"[#{memory_id}] Sign emails as Fisher X." in system
    assert "Sign emails as Fisher." not in system


def test_the_agent_forgets_the_memory_it_names_and_keeps_the_others(tmp_path):
    model = FakeProvider(
        steps=[
            _call("memory_forget", memory_id=2),
            {"deltas": ("Forgot #2.",)},
            {"deltas": ("Ok.",)},
        ]
    )
    client = _client(tmp_path, model)
    for content in ("Prefer short subject lines.", "Never email on Sundays."):
        client.post("/v1/memory", json={"content": content}, headers=HEADERS)

    events = _turn(client, "forget the Sunday rule")
    assert model.calls[0][0]["content"].count("sourcecado:memory/") == 2
    forgot = _finished(events, "memory_forget")
    assert forgot["result"] == {"forgotten": True, "id": 2}

    _turn(client, "what are my rules?")
    system = model.calls[-1][0]["content"]
    assert "[#1] Prefer short subject lines." in system
    assert "Sundays" not in system


def test_a_deleted_memory_never_reappears(tmp_path):
    model = FakeProvider(
        steps=[
            _call("memory_update", memory_id=1, content="Bring it back."),
            {"deltas": ("It is gone.",)},
        ]
    )
    client = _client(tmp_path, model)
    client.post("/v1/memory", json={"content": "Old rule to delete."}, headers=HEADERS)

    assert client.delete("/v1/memory/1", headers=HEADERS).status_code == 200
    events = _turn(client, "update memory 1")
    revived = _finished(events, "memory_update")
    assert revived["ok"] is False
    assert "Old rule" not in model.calls[0][0]["content"]

    replacement = client.post(
        "/v1/memory", json={"content": "A new rule."}, headers=HEADERS
    ).json()["memory"]
    assert replacement["id"] == 2

    restarted = _client(tmp_path, FakeProvider(deltas=("Ok.",)))
    contents = [
        item["content"]
        for item in restarted.get("/v1/memory", headers=HEADERS).json()["items"]
    ]
    assert contents == ["A new rule."]
    assert not (tmp_path / "memory" / "1.md").exists()
    assert "Old rule" not in (tmp_path / "memory" / "MEMORY.md").read_text()


def test_memory_saves_reject_empty_and_text_the_prompt_would_clip(tmp_path):
    client = _client(tmp_path, FakeProvider())

    assert client.post("/v1/memory", json={"content": "  "}, headers=HEADERS).status_code == 400
    too_long = client.post("/v1/memory", json={"content": "a" * 141}, headers=HEADERS)
    assert too_long.status_code == 422
    assert "140" in too_long.json()["error"]
    assert client.post("/v1/memory", json={"content": "a" * 140}, headers=HEADERS).status_code == 200
    missing = client.patch("/v1/memory/99", json={"content": "x"}, headers=HEADERS)
    assert missing.status_code == 404
    assert client.get("/v1/memory", headers=HEADERS).json()["max_chars"] == 140
