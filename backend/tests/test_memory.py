from coworker.store import ConversationStore


def test_remember_list_update_forget(tmp_path):
    store = ConversationStore(tmp_path)
    item = store.remember("Fisher drinks oat lattes")
    assert item["id"] == 1
    assert item["content"] == "Fisher drinks oat lattes"
    assert store.list_memories() == [item]

    updated = store.memory_update(1, "Fisher drinks oat lattes, extra shot")
    assert updated is not None
    assert updated["content"] == "Fisher drinks oat lattes, extra shot"
    assert store.list_memories()[0]["content"].endswith("extra shot")

    assert store.memory_forget(1) is True
    assert store.list_memories() == []


def test_update_and_forget_missing(tmp_path):
    store = ConversationStore(tmp_path)
    assert store.memory_update(9, "nope") is None
    assert store.memory_forget(9) is False
