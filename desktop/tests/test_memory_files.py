from coworker.store import ConversationStore


def test_remember_writes_markdown(tmp_path):
    store = ConversationStore(tmp_path)
    item = store.remember("likes matcha")
    path = tmp_path / "memory" / f"{item['id']}.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "likes matcha" in text
    assert str(item["id"]) in text


def test_update_and_forget_markdown(tmp_path):
    store = ConversationStore(tmp_path)
    item = store.remember("old")
    path = tmp_path / "memory" / f"{item['id']}.md"
    store.memory_update(item["id"], "new fact")
    assert "new fact" in path.read_text(encoding="utf-8")
    store.memory_forget(item["id"])
    assert not path.exists()


def test_remember_rebuilds_memory_md(tmp_path):
    store = ConversationStore(tmp_path)
    a = store.remember("likes matcha")
    b = store.remember("Alyssa is at Berkeley")
    text = (tmp_path / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert f"[#{a['id']}] likes matcha" in text
    assert f"[#{b['id']}] Alyssa is at Berkeley" in text


def test_forget_removes_index_line(tmp_path):
    store = ConversationStore(tmp_path)
    a = store.remember("gone soon")
    store.memory_forget(a["id"])
    text = (tmp_path / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "gone soon" not in text
    assert not (tmp_path / "memory" / f"{a['id']}.md").exists()


def test_system_prompt_uses_index_when_over_cap(tmp_path):
    from coworker.server import system_prompt

    store = ConversationStore(tmp_path)
    for i in range(80):
        store.remember("x" * 60 + f" {i}")
    prompt = system_prompt(store)
    assert "Memory index" in prompt or "[#1]" in prompt
    assert len(prompt) < 20000
