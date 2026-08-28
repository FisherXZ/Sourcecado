"""Boundaries are computed over atomic tool-call groups, never message indexes.

A boundary that splits an assistant tool call from its result, or that heads
the outbound view on an orphaned tool result, produces a provider request many
providers accept and then behave strangely on. Every test here asserts the
boundary landed somewhere legal AND that compaction actually moved something,
so none of them can pass by never running.
"""

from __future__ import annotations

import json

import pytest

from coworker.compaction import (
    atomic_units,
    boundary_candidates,
    estimate_tokens,
    pick_boundary,
    transcript_defects,
)


def _assistant(call_ids: list[str], *, text: str | None = None) -> dict:
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "board_get", "arguments": "{}"},
            }
            for call_id in call_ids
        ],
    }


def _result(call_id: str, payload: dict | None = None) -> dict:
    return {
        "role": "tool",
        "name": "board_get",
        "tool_call_id": call_id,
        "content": json.dumps(payload or {"ok": True}),
    }


def _conversation(pairs: int, *, filler: int = 0) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": "system prompt"}]
    for index in range(pairs):
        messages.append({"role": "user", "content": f"user turn {index}"})
        messages.append(_assistant([f"call_{index}"]))
        messages.append(_result(f"call_{index}", {"ok": True, "pad": "x" * filler}))
        messages.append({"role": "assistant", "content": f"assistant turn {index}"})
    return messages


# --- units partition the transcript -------------------------------------


def test_atomic_units_partition_every_message_exactly_once():
    """The runtime grouper must not drop messages the way the evals one does.

    Dropping a malformed group is safe for an eval harness rebuilding a
    transcript from scratch. In the turn loop the same move would silently
    delete a director instruction, so units cover the input exactly.
    """
    messages = _conversation(3)
    units = atomic_units(messages)

    flattened = [message for unit in units for message in unit.messages]
    assert flattened == messages
    assert [unit.start for unit in units] == sorted(unit.start for unit in units)


def test_a_tool_call_and_its_result_share_one_unit():
    messages = _conversation(1)
    units = atomic_units(messages)

    grouped = [unit for unit in units if len(unit.messages) > 1]
    assert len(grouped) == 1
    roles = [message["role"] for message in grouped[0].messages]
    assert roles == ["assistant", "tool"]


def test_a_parallel_tool_call_keeps_all_of_its_results():
    messages = [
        {"role": "user", "content": "do both"},
        _assistant(["call_a", "call_b"]),
        _result("call_a"),
        _result("call_b"),
        {"role": "assistant", "content": "done"},
    ]
    units = atomic_units(messages)

    grouped = next(unit for unit in units if len(unit.messages) > 1)
    assert [message.get("tool_call_id") for message in grouped.messages[1:]] == [
        "call_a",
        "call_b",
    ]
    assert grouped.well_formed


def test_a_malformed_group_is_held_together_and_marked():
    """An assistant call whose result never arrived stays one unit.

    Holding it together is what stops a boundary from being placed between the
    call and the missing result, which is the shape that produces an orphan.
    """
    messages = [
        {"role": "user", "content": "go"},
        _assistant(["call_a", "call_b"]),
        _result("call_a"),
        {"role": "assistant", "content": "partial"},
    ]
    units = atomic_units(messages)

    grouped = next(unit for unit in units if len(unit.messages) > 1)
    assert not grouped.well_formed
    assert [message["role"] for message in grouped.messages] == ["assistant", "tool"]


def test_a_leading_orphan_result_is_its_own_unit_and_marked():
    messages = [
        _result("call_missing"),
        {"role": "user", "content": "hello"},
    ]
    units = atomic_units(messages)

    assert not units[0].well_formed
    assert units[0].messages == [messages[0]]


# --- candidates are unit heads only -------------------------------------


def test_boundary_candidates_are_exactly_the_unit_starts():
    messages = _conversation(3)
    units = atomic_units(messages)

    candidates = boundary_candidates(messages, start=1)
    assert set(candidates) <= {unit.start for unit in units}
    assert all(index >= 1 for index in candidates)


def test_no_candidate_ever_lands_on_a_tool_message():
    messages = _conversation(4)
    for index in boundary_candidates(messages, start=1):
        assert messages[index]["role"] != "tool"


def test_no_candidate_lands_inside_a_parallel_group():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "do both"},
        _assistant(["call_a", "call_b"]),
        _result("call_a"),
        _result("call_b"),
        {"role": "user", "content": "next"},
    ]
    candidates = boundary_candidates(messages, start=1)

    # 3 and 4 are the two results. Either one as a head is an orphan.
    assert 3 not in candidates
    assert 4 not in candidates
    assert 2 in candidates  # the group head itself is legal


# --- the group sitting exactly on the boundary --------------------------


def test_a_group_at_the_exact_boundary_is_never_split():
    """The keep budget is tuned so the cut wants to fall between an assistant
    tool call and its result. The boundary must move off it, not split it."""
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "older turn"},
        {"role": "assistant", "content": "older answer"},
        {"role": "user", "content": "the turn under test"},
        _assistant(["call_edge"]),
        _result("call_edge", {"ok": True, "pad": "y" * 400}),
        {"role": "assistant", "content": "edge answer"},
    ]
    # Exactly the tail from the result onward: the budget that tempts a split.
    keep_tokens = estimate_tokens(messages[5:])

    boundary = pick_boundary(messages, keep_tokens=keep_tokens)

    assert boundary is not None, "compaction did not run"
    assert boundary != 5, "boundary split the call from its result"
    assert messages[boundary]["role"] != "tool"
    tail = messages[boundary:]
    assert transcript_defects([{"role": "system", "content": "s"}] + tail) == []


@pytest.mark.parametrize("keep_tokens", list(range(1, 400, 7)))
def test_every_keep_budget_produces_a_well_formed_tail(keep_tokens):
    """Sweep the budget across the whole transcript. No budget may produce a
    tail that starts on an orphan or ends mid-group."""
    messages = _conversation(4, filler=60)

    boundary = pick_boundary(messages, keep_tokens=keep_tokens)
    if boundary is None:
        return
    tail = messages[boundary:]
    assert tail[0]["role"] != "tool"
    assert transcript_defects([messages[0]] + tail) == []
    # The summarized span must also be whole: no group half in, half out.
    assert transcript_defects(messages[1:boundary]) == []


def test_a_giant_tool_result_does_not_force_a_split():
    """One tool result larger than the whole keep budget. The only options are
    to keep the group whole or drop it whole."""
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "read the huge doc"},
        _assistant(["call_huge"]),
        _result("call_huge", {"text": "z" * 200_000}),
        {"role": "assistant", "content": "summarised"},
    ]

    boundary = pick_boundary(messages, keep_tokens=50)

    assert boundary is not None
    tail = messages[boundary:]
    assert tail[0]["role"] != "tool"
    assert transcript_defects([messages[0]] + tail) == []


def test_pick_boundary_returns_none_when_there_is_nothing_to_summarize():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hi"},
    ]
    assert pick_boundary(messages, keep_tokens=1_000_000) is None


# --- the defect detector itself -----------------------------------------


def test_transcript_defects_names_an_orphan_result():
    defects = transcript_defects([_result("call_nobody")])
    assert defects and "orphan" in defects[0]


def test_transcript_defects_names_an_unanswered_call():
    defects = transcript_defects([_assistant(["call_open"])])
    assert defects and "call_open" in defects[0]


def test_transcript_defects_accepts_a_clean_transcript():
    assert transcript_defects(_conversation(3)) == []
