import copy

import pytest

from coworker.agent_run_state import (
    AgentRunTransitionError,
    approval_ready_transition,
    approval_resolved_transition,
    initial_continuation,
    model_completed_transition,
    model_pending_transition,
    terminal_transition,
    tool_completed_transition,
    tool_pending_transition,
    waiting_approval_transition,
)


IDENTITY = {"message_id": "message-state", "part_id": "part-state"}


def _initial():
    return initial_continuation(IDENTITY, 3)


def _model_pending(snapshot=None, step=0):
    return model_pending_transition(
        snapshot or _initial(), "run-state", [], [], step
    )


def _tools_ready(tool_count=2):
    return model_completed_transition(
        _model_pending(),
        "run-state",
        [],
        [],
        0,
        tool_count,
        0,
        IDENTITY["message_id"],
    )


def _tool_pending(snapshot=None, tool_index=0, call_id=None):
    return tool_pending_transition(
        snapshot or _tools_ready(),
        "run-state",
        [],
        [],
        0,
        tool_index,
        call_id or f"call-{tool_index}",
        "search",
        True,
    )


def _tool_completed(snapshot=None, tool_index=0, call_id=None):
    return tool_completed_transition(
        snapshot or _tool_pending(tool_index=tool_index, call_id=call_id),
        "run-state",
        [],
        [],
        0,
        tool_index,
        call_id or f"call-{tool_index}",
        "search",
        True,
        "a" * 64,
    )


def test_allowed_transition_path_tracks_exact_tool_count_and_next_step():
    model = _model_pending()
    assert model["cursor"]["phase"] == "model_in_flight"
    assert model["pending_model"] == {
        "attempt_id": "run-state:0:model",
        "status": "in_flight",
        "budget_reserved": True,
    }
    assert model["remaining_budgets"]["work_steps"] == 2

    tools = model_completed_transition(
        model, "run-state", [], [], 0, 2, 0, IDENTITY["message_id"]
    )
    assert tools["cursor"]["expected_tool_count"] == 2
    first = _tool_pending(tools, 0, "call-0")
    first_done = _tool_completed(first, 0, "call-0")
    second = _tool_pending(first_done, 1, "call-1")
    all_done = _tool_completed(second, 1, "call-1")

    next_model = model_pending_transition(
        all_done, "run-state", [], [], 1
    )
    assert next_model["cursor"] == {
        "phase": "model_in_flight",
        "step_index": 1,
        "next_tool_index": 0,
        "expected_tool_count": 0,
        **{
            key: all_done["cursor"][key]
            for key in (
                "transcript_prefix_count",
                "transcript_prefix_sha256",
                "event_prefix_count",
                "event_prefix_sha256",
            )
        },
    }


def test_zero_tool_model_becomes_terminal_ready_and_can_complete():
    ready = model_completed_transition(
        _model_pending(),
        "run-state",
        [],
        [],
        0,
        0,
        4,
        IDENTITY["message_id"],
    )

    terminal = terminal_transition(
        ready, [], [], "complete", IDENTITY["message_id"], 4
    )

    assert ready["cursor"]["phase"] == "terminal_ready"
    assert terminal["cursor"]["phase"] == "complete"


def test_approval_wait_and_resolution_return_to_identical_tool_cursor():
    tools = _tools_ready(1)
    waiting = waiting_approval_transition(
        tools,
        "run-state",
        [],
        [],
        "approval-1",
        0,
        0,
        "call-approved",
        "gmail_draft",
        False,
    )
    ready = approval_ready_transition(waiting, "approval-1", "allow")

    resumed = approval_resolved_transition(
        ready, [], [], "approval-1"
    )

    assert waiting["cursor"]["phase"] == "waiting_approval"
    assert waiting["pending_tool"]["budget_reserved"] is False
    assert ready["cursor"]["phase"] == "approval_ready"
    assert resumed["cursor"] == {
        **ready["cursor"],
        "phase": "tools_ready",
    }
    assert "pending_interaction" not in resumed
    assert resumed["pending_tool"] == waiting["pending_tool"]
    reserved = tool_pending_transition(
        resumed,
        "run-state",
        [],
        [],
        0,
        0,
        "call-approved",
        "gmail_draft",
        False,
    )
    assert reserved["pending_tool"]["budget_reserved"] is True
    assert reserved["remaining_budgets"]["tool_calls"] == 2


def test_denied_approval_advances_without_tool_budget_or_execution():
    waiting = waiting_approval_transition(
        _tools_ready(1),
        "run-state",
        [],
        [],
        "approval-deny",
        0,
        0,
        "call-deny",
        "gmail_draft",
        False,
    )
    ready = approval_ready_transition(waiting, "approval-deny", "deny")

    denied = approval_resolved_transition(
        ready, [], [], "approval-deny"
    )

    assert denied["cursor"]["next_tool_index"] == 1
    assert denied["remaining_budgets"]["tool_calls"] == 3
    assert "pending_tool" not in denied
    assert denied["completed_tool_receipts"][-1]["outcome"] == "denied"
    next_model = model_pending_transition(
        denied, "run-state", [], [], 1
    )
    assert next_model["cursor"]["phase"] == "model_in_flight"


@pytest.mark.parametrize(
    "transition",
    (
        lambda: model_pending_transition(_initial(), "run-state", [], [], 1),
        lambda: model_completed_transition(
            _initial(), "run-state", [], [], 0, 0, 0, IDENTITY["message_id"]
        ),
        lambda: model_completed_transition(
            _model_pending(),
            "run-state",
            [],
            [],
            0,
            -1,
            0,
            IDENTITY["message_id"],
        ),
        lambda: _tool_pending(_model_pending()),
        lambda: _tool_pending(_tools_ready(), 1, "call-1"),
        lambda: _tool_pending(_tool_completed(_tools_ready(1)), 1, "call-1"),
        lambda: model_pending_transition(
            _tool_completed(_tools_ready(2)), "run-state", [], [], 1
        ),
        lambda: waiting_approval_transition(
            _model_pending(),
            "run-state",
            [],
            [],
            "approval-1",
            0,
            0,
            "call-1",
            "gmail_draft",
            False,
        ),
        lambda: waiting_approval_transition(
            model_completed_transition(
                _model_pending(),
                "run-state",
                [],
                [],
                0,
                0,
                0,
                IDENTITY["message_id"],
            ),
            "run-state",
            [],
            [],
            "approval-1",
            0,
            0,
            "call-1",
            "gmail_draft",
            False,
        ),
        lambda: waiting_approval_transition(
            waiting_approval_transition(
                _tools_ready(1),
                "run-state",
                [],
                [],
                "approval-1",
                0,
                0,
                "call-1",
                "gmail_draft",
                False,
            ),
            "run-state",
            [],
            [],
            "approval-2",
            0,
            0,
            "call-1",
            "gmail_draft",
            False,
        ),
        lambda: terminal_transition(
            _tools_ready(1), [], [], "complete", IDENTITY["message_id"], 0
        ),
    ),
)
def test_forbidden_transitions_are_rejected(transition):
    with pytest.raises(AgentRunTransitionError):
        transition()


def test_model_completion_requires_exact_deterministic_attempt():
    corrupted = copy.deepcopy(_model_pending())
    corrupted["pending_model"]["attempt_id"] = "another-run:0:model"

    with pytest.raises(AgentRunTransitionError, match="attempt"):
        model_completed_transition(
            corrupted,
            "run-state",
            [],
            [],
            0,
            0,
            0,
            IDENTITY["message_id"],
        )


@pytest.mark.parametrize(
    ("snapshot", "state"),
    ((_model_pending(), "failed"), (_tools_ready(1), "stopped")),
)
def test_failed_and_stopped_terminal_are_explicitly_allowed_from_active_phases(
    snapshot, state
):
    terminal = terminal_transition(
        snapshot, [], [], state, IDENTITY["message_id"], 0
    )

    assert terminal["cursor"]["phase"] == state
