"""The Agent Run state machine: which checkpoint may move a run where.

One table drives everything. Each checkpoint kind declares the states it may be
appended from and the states it may leave the run in. State reachability is
derived from that table rather than restated, so a run store, a resume path, and
Doctor all read the same rules and cannot drift apart.
"""

from __future__ import annotations

from coworker.agent_runs import (
    AGENT_RUN_STATES,
    CHECKPOINT_KINDS,
    LEASABLE_AGENT_RUN_STATES,
    TERMINAL_AGENT_RUN_STATES,
    WAITING_AGENT_RUN_STATES,
)

_RUNNING = frozenset({"running"})
_ACTIVE = LEASABLE_AGENT_RUN_STATES
_INTERRUPTED = frozenset({"interrupted"})


class AgentRunTransitionError(ValueError):
    """A checkpoint would move a run along an edge the state machine forbids."""


# kind -> (states it may be appended from, states it may leave behind)
_EDGES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "run_started": (_RUNNING, _RUNNING),
    "model_pending": (_ACTIVE, _RUNNING),
    "model_completed": (_RUNNING, _RUNNING),
    "tool_pending": (_RUNNING, _RUNNING),
    "tool_completed": (_RUNNING, _RUNNING),
    # An unknown outcome may only stall or end the run, never silently continue.
    "tool_outcome_unknown": (_ACTIVE, _INTERRUPTED | frozenset({"partial"})),
    "waiting_approval": (_RUNNING, frozenset({"waiting_approval"})),
    "waiting_input": (_RUNNING, frozenset({"waiting_input"})),
    "waiting_external": (_RUNNING, frozenset({"waiting_external"})),
    "approval_resolved": (WAITING_AGENT_RUN_STATES, _RUNNING),
    "process_interrupted": (_ACTIVE, _INTERRUPTED),
    "terminal": (_ACTIVE | WAITING_AGENT_RUN_STATES, TERMINAL_AGENT_RUN_STATES),
}

CHECKPOINT_FROM_STATES: dict[str, frozenset[str]] = {
    kind: edge[0] for kind, edge in _EDGES.items()
}
CHECKPOINT_STATES: dict[str, frozenset[str]] = {
    kind: edge[1] for kind, edge in _EDGES.items()
}
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    state: frozenset().union(
        *(to for allowed_from, to in _EDGES.values() if state in allowed_from),
        frozenset(),
    )
    for state in AGENT_RUN_STATES
}

assert set(_EDGES) == CHECKPOINT_KINDS, "checkpoint kinds and edges disagree"


def is_terminal(state: str) -> bool:
    return state in TERMINAL_AGENT_RUN_STATES


def is_waiting(state: str) -> bool:
    return state in WAITING_AGENT_RUN_STATES


def is_leasable(state: str) -> bool:
    """Whether a process may hold execution authority over a run in this state."""
    return state in LEASABLE_AGENT_RUN_STATES


def validate_transition(kind: str, from_state: str, to_state: str) -> None:
    """Raise unless this exact checkpoint edge exists."""
    if kind not in _EDGES:
        raise AgentRunTransitionError(f"unknown checkpoint kind {kind!r}")
    if from_state not in AGENT_RUN_STATES:
        raise AgentRunTransitionError(f"unknown run state {from_state!r}")
    if to_state not in AGENT_RUN_STATES:
        raise AgentRunTransitionError(f"unknown run state {to_state!r}")
    allowed_from, allowed_to = _EDGES[kind]
    if from_state not in allowed_from:
        raise AgentRunTransitionError(
            f"checkpoint {kind!r} cannot be appended from {from_state!r}"
        )
    if to_state not in allowed_to:
        raise AgentRunTransitionError(
            f"checkpoint {kind!r} cannot leave a run in {to_state!r}"
        )
