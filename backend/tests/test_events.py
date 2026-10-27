import pytest

from coworker.events import EVENT_VERSION, TurnIdentity, build_event, validate_event


def test_text_delta_uses_the_canonical_v2_event_envelope():
    identity = TurnIdentity(
        session_id="session-alpha",
        run_id="run-1",
        message_id="message-1",
        part_id="part-1",
    )

    event = build_event(
        identity,
        "assistant_delta",
        event_id="event-1",
        delta="Hello",
    )

    assert EVENT_VERSION == 2
    assert event == {
        "version": 2,
        "type": "assistant_delta",
        "session_id": "session-alpha",
        "run_id": "run-1",
        "event_id": "event-1",
        "message_id": "message-1",
        "part_id": "part-1",
        "delta": "Hello",
    }


def test_contract_reports_malformed_and_unknown_version_events():
    malformed = {
        "version": 2,
        "type": "assistant_delta",
        "session_id": "session-alpha",
        "run_id": "run-1",
        "event_id": "event-1",
        "message_id": "message-1",
        "delta": "Hello",
    }
    unknown_version = {
        **malformed,
        "version": 99,
        "part_id": "part-1",
    }

    assert validate_event(malformed) == "missing part_id"
    assert validate_event(unknown_version) == "unsupported version 99"


def test_builder_rejects_an_unknown_top_level_event_type():
    identity = TurnIdentity(
        session_id="session-alpha",
        run_id="run-1",
        message_id="message-1",
        part_id="part-1",
    )

    with pytest.raises(ValueError, match="unsupported event type future_event"):
        build_event(identity, "future_event", event_id="event-1")


def test_contract_rejects_a_text_delta_without_string_content():
    malformed = {
        "version": 2,
        "type": "assistant_delta",
        "session_id": "session-alpha",
        "run_id": "run-1",
        "event_id": "event-1",
        "message_id": "message-1",
        "part_id": "part-1",
        "delta": None,
    }

    assert validate_event(malformed) == "assistant_delta.delta must be a string"


@pytest.mark.parametrize(
    ("payload", "problem"),
    [
        (
            {"type": "turn_start"},
            "turn_start.state must be running",
        ),
        (
            {"type": "turn_end", "state": "cancelled", "text": "partial"},
            "turn_end.state must be complete, partial, stopped, or interrupted",
        ),
        (
            {"type": "error", "state": "complete", "message": "failed"},
            "error.state must be failed or held",
        ),
        (
            # `held` is the one terminal that points at something an operator
            # has to open. Without the effect id it points at nothing.
            {"type": "error", "state": "held", "message": "unknown"},
            "error.code must be outcome_unknown when state is held",
        ),
        (
            {
                "type": "error",
                "state": "held",
                "code": "outcome_unknown",
                "message": "unknown",
            },
            "error.effect_id must be a non-empty string when state is held",
        ),
        (
            {
                "type": "tool_started",
                "id": "call-1",
                "name": "now",
                "arguments": [],
            },
            "tool_started.arguments must be an object",
        ),
        (
            {"type": "reasoning_delta", "delta": "", "step": 1, "at": "t"},
            "reasoning_delta.delta must be a non-empty string",
        ),
        (
            {"type": "reasoning_delta", "delta": "x", "step": 0, "at": "t"},
            "reasoning_delta.step must be a positive integer",
        ),
        (
            {"type": "reasoning_delta", "delta": "x", "step": 1},
            "reasoning_delta.at must be a non-empty string",
        ),
    ],
)
def test_contract_enforces_event_specific_payload_shapes(payload, problem):
    event = {
        "version": 2,
        "session_id": "session-alpha",
        "run_id": "run-1",
        "event_id": "event-1",
        "message_id": "message-1",
        "part_id": "part-1",
        **payload,
    }

    assert validate_event(event) == problem


def test_reasoning_delta_uses_the_canonical_v2_event_envelope():
    identity = TurnIdentity(
        session_id="session-alpha",
        run_id="run-1",
        message_id="message-1",
        part_id="part-1",
    )

    event = build_event(
        identity,
        "reasoning_delta",
        event_id="event-1",
        delta="Checking the shortlist.",
        step=1,
        at="2026-10-26T12:00:00+00:00",
    )

    assert event == {
        "version": 2,
        "type": "reasoning_delta",
        "session_id": "session-alpha",
        "run_id": "run-1",
        "event_id": "event-1",
        "message_id": "message-1",
        "part_id": "part-1",
        "delta": "Checking the shortlist.",
        "step": 1,
        "at": "2026-10-26T12:00:00+00:00",
    }
