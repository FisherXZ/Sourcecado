"""Slice 1: Agent Run vocabulary, checkpoint redaction, and transition rules."""

import hashlib

import pytest

from coworker.agent_run_state import (
    AgentRunTransitionError,
    CHECKPOINT_STATES,
    LEGAL_TRANSITIONS,
    is_leasable,
    is_terminal,
    is_waiting,
    validate_transition,
)
from coworker.agent_runs import (
    AGENT_RUN_STATES,
    CHECKPOINT_KINDS,
    CHECKPOINT_PAYLOAD_FIELDS,
    LEASABLE_AGENT_RUN_STATES,
    RUN_TRIGGERS,
    TERMINAL_AGENT_RUN_STATES,
    WAITING_AGENT_RUN_STATES,
    add_usage,
    checkpoint_payload,
    goal_fingerprint,
    merge_unique_refs,
    merge_unique_strings,
    project_artifact_refs,
    project_source_refs,
    project_terminal_result,
    redact_secrets,
)


def test_state_vocabulary_partitions_terminal_waiting_and_leasable():
    assert TERMINAL_AGENT_RUN_STATES < AGENT_RUN_STATES
    assert WAITING_AGENT_RUN_STATES < AGENT_RUN_STATES
    assert LEASABLE_AGENT_RUN_STATES < AGENT_RUN_STATES
    assert not TERMINAL_AGENT_RUN_STATES & WAITING_AGENT_RUN_STATES
    assert not LEASABLE_AGENT_RUN_STATES & (
        TERMINAL_AGENT_RUN_STATES | WAITING_AGENT_RUN_STATES
    )
    assert (
        LEASABLE_AGENT_RUN_STATES
        | TERMINAL_AGENT_RUN_STATES
        | WAITING_AGENT_RUN_STATES
        == AGENT_RUN_STATES
    )
    assert RUN_TRIGGERS == {"chat", "queued_chat", "scheduled"}
    for state in AGENT_RUN_STATES:
        assert is_terminal(state) == (state in TERMINAL_AGENT_RUN_STATES)
        assert is_waiting(state) == (state in WAITING_AGENT_RUN_STATES)
        assert is_leasable(state) == (state in LEASABLE_AGENT_RUN_STATES)


def test_every_checkpoint_kind_declares_the_states_it_may_leave_behind():
    assert set(CHECKPOINT_STATES) == CHECKPOINT_KINDS
    for kind, states in CHECKPOINT_STATES.items():
        assert states <= AGENT_RUN_STATES, kind
        assert states, kind
    assert set(LEGAL_TRANSITIONS) == AGENT_RUN_STATES
    for state in TERMINAL_AGENT_RUN_STATES:
        assert LEGAL_TRANSITIONS[state] == frozenset()


@pytest.mark.parametrize(
    "kind,from_state,to_state",
    [
        ("run_started", "running", "running"),
        ("model_completed", "running", "running"),
        ("waiting_approval", "running", "waiting_approval"),
        ("approval_resolved", "waiting_approval", "running"),
        ("process_interrupted", "running", "interrupted"),
        ("model_pending", "interrupted", "running"),
        ("terminal", "running", "complete"),
        ("terminal", "waiting_external", "partial"),
    ],
)
def test_legal_transitions_are_accepted(kind, from_state, to_state):
    validate_transition(kind, from_state, to_state)


@pytest.mark.parametrize(
    "kind,from_state,to_state",
    [
        # A finished run never moves again.
        ("model_pending", "complete", "running"),
        ("terminal", "failed", "complete"),
        # A checkpoint may not leave a state its kind does not describe.
        ("model_completed", "running", "waiting_approval"),
        ("waiting_approval", "running", "running"),
        # Waiting runs resolve through their own kind, not a bare model step.
        ("model_pending", "waiting_approval", "running"),
        # Unknown vocabulary is a programming error, never a silent write.
        ("teleport", "running", "running"),
        ("terminal", "running", "elsewhere"),
    ],
)
def test_impossible_transitions_are_rejected(kind, from_state, to_state):
    with pytest.raises(AgentRunTransitionError):
        validate_transition(kind, from_state, to_state)


def test_checkpoint_payload_drops_everything_outside_the_allowlist():
    payload = checkpoint_payload(
        {
            "step": 3,
            "tool_name": "apollo_search",
            "message": "the operator's private message body",
            "content": "assistant prose",
            "text": "assistant prose",
            "reasoning": "raw chain of thought",
            "thinking": "raw chain of thought",
            "arguments": {"query": "private"},
            "result": {"rows": ["private tool output"]},
            "api_key": "sk-proj-AAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "authorization": "Bearer abcdefghijklmnopqrst",
            "headers": {"Authorization": "Bearer abcdefghijklmnopqrst"},
        }
    )
    assert payload == {"step": 3, "tool_name": "apollo_search"}
    assert set(payload) <= CHECKPOINT_PAYLOAD_FIELDS


def test_checkpoint_payload_redacts_secrets_inside_allowed_fields():
    payload = checkpoint_payload(
        {
            "error_summary": (
                "provider refused key sk-proj-AAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            ),
            "reason": "token=hunter2-hunter2-hunter2",
            "status": "failed",
            "text_length": 4096,
            "usage": {"input_tokens": 10, "output_tokens": 2, "junk": "x"},
            "source_ref_ids": ["src-1", "src-2", ""],
        }
    )
    assert "sk-proj-AAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in payload["error_summary"]
    assert "[REDACTED]" in payload["error_summary"]
    assert payload["reason"] == "token=[REDACTED]"
    assert payload["status"] == "failed"
    assert payload["text_length"] == 4096
    assert payload["usage"] == {"input_tokens": 10, "output_tokens": 2}
    assert payload["source_ref_ids"] == ["src-1", "src-2"]


def test_checkpoint_payload_coerces_and_bounds_allowed_fields():
    payload = checkpoint_payload(
        {
            "step": "7",
            "text_length": -12,
            "duration_ms": 3.9,
            "error_summary": "x" * 5000,
            "tool_call_ids": [f"call-{i}" for i in range(200)],
            "item_count": None,
        }
    )
    assert payload["step"] == 7
    assert payload["text_length"] == 0
    assert payload["duration_ms"] == 3
    assert len(payload["error_summary"]) == 512
    assert len(payload["tool_call_ids"]) == 50
    assert "item_count" not in payload
    assert checkpoint_payload(None) == {}
    assert checkpoint_payload("not a mapping") == {}


@pytest.mark.parametrize(
    "planted",
    [
        "sk-proj-AAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "AKIAIOSFODNN7EXAMPLE",
        # Assembled rather than written out: a Slack-shaped literal trips GitHub
        # push protection even when the digits are obvious padding. The redactor
        # still receives the identical string.
        "-".join(("xoxb", "1" * 10, "2" * 10, "a" * 20)),
        "eyJhbGciOi.eyJzdWIiOi.AAAAAAAAAAAA",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_redact_secrets_masks_known_credential_shapes(planted):
    redacted = redact_secrets(f"call failed with {planted} attached")
    assert planted not in redacted
    assert "REDACTED" in redacted


def test_redact_secrets_masks_credential_assignments_and_headers():
    assert redact_secrets("password: hunter2") == "password: [REDACTED]"
    assert redact_secrets('{"Authorization": "Bearer abc.def"}') == (
        '{"Authorization": "[REDACTED]"}'
    )
    assert redact_secrets("CLUB_API_TOKEN=abc123") == "CLUB_API_TOKEN=[REDACTED]"
    assert redact_secrets("nothing to hide here") == "nothing to hide here"


def test_goal_fingerprint_is_a_digest_and_never_the_goal():
    goal = "Find three Codeology leads at Ramp"
    digest = goal_fingerprint(goal)
    assert digest == hashlib.sha256(goal.encode("utf-8")).hexdigest()
    assert goal not in digest
    assert goal_fingerprint(goal) == digest
    assert goal_fingerprint(goal + "!") != digest


def test_source_and_artifact_refs_keep_provenance_and_drop_url_credentials():
    sources = project_source_refs(
        [
            {
                "id": "src-1",
                "title": "Ramp engineering",
                "url": "https://example.com/a?access_token=abcdefghijklmnop#frag",
                "provider": "tavily",
                "stale": False,
                "raw_body": "the whole scraped page",
            },
            "not a mapping",
        ]
    )
    assert sources == [
        {
            "id": "src-1",
            "title": "Ramp engineering",
            "url": "https://example.com/a",
            "provider": "tavily",
            "stale": False,
        }
    ]
    artifacts = project_artifact_refs(
        [{"id": "art-1", "artifact_type": "brief", "title": "Ramp brief", "body": "x"}]
    )
    assert artifacts == [
        {
            "id": "art-1",
            "artifact_type": "brief",
            "title": "Ramp brief",
            "external_url": None,
        }
    ]
    assert project_source_refs([{"id": "s", "url": "javascript:alert(1)"}])[0][
        "url"
    ] is None


def test_terminal_result_records_shape_not_content():
    projected = project_terminal_result(
        {
            "status": "complete",
            "message_id": "msg-9",
            "text": "the full assistant answer",
            "error": "failed with token=abcdef",
        }
    )
    assert projected == {
        "status": "complete",
        "message_id": "msg-9",
        "text_length": len("the full assistant answer"),
        "error": "failed with token=[REDACTED]",
    }
    assert project_terminal_result(None) is None


def test_correlation_merges_are_first_seen_and_deduplicated():
    merged = merge_unique_refs(
        [{"id": "a", "title": "first"}], [{"id": "a", "title": "second"}, {"id": "b"}]
    )
    assert merged == [{"id": "a", "title": "first"}, {"id": "b"}]
    assert merge_unique_strings(["ap-1"], ["ap-1", "ap-2", " "]) == ["ap-1", "ap-2"]
    assert add_usage({"input_tokens": 5}, {"input_tokens": 3, "cost_usd": 0.5}) == {
        "input_tokens": 8,
        "cost_usd": 0.5,
    }
    assert add_usage({"input_tokens": 5}, {"input_tokens": True}) == {"input_tokens": 5}
