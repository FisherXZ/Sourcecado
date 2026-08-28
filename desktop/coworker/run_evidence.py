"""How well a run's record supports a conclusion, and when it supports none.

Silence is not absence. "No approval was requested" and "we do not know whether
an approval was requested" are different operator situations, so they are
different values here and one can never decay into the other.

The separation is a property of the record, computed once before any section of
a receipt is read. A run's checkpoint sequence is dense from 1 to
`checkpoint_sequence`, so the stored rows say whether the record covers the
run's whole life. Everything else follows from that.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable

from coworker.agent_run_state import is_terminal
from coworker.agent_runs import (
    AGENT_RUN_STATES,
    CHECKPOINT_KINDS,
    INCOMPLETE_RECORD_KINDS,
    RUN_TRIGGERS,
)


class Evidence(StrEnum):
    """How well the record supports one facet of a run. Never inferred.

    `absent` and `missing` are the pair this vocabulary exists for: "no
    approval was requested" and "we do not know whether one was" must not
    render the same way.
    """

    PRESENT = "present"
    # Positively did not happen: the record covers the run's whole life.
    ABSENT = "absent"
    # Some of the facet settled and some did not.
    PARTIAL = "partial"
    # Should be knowable and is not. The record has a hole, or the run is open.
    MISSING = "missing"
    # The run knows it does not know: an external effect never reported back.
    AMBIGUOUS = "ambiguous"
    # This build cannot express or verify the fact. Never "it did not happen".
    UNSUPPORTED = "unsupported"
    # The evidence existed and retention aged it out on purpose.
    EXPIRED = "expired"


_SEVERITY = {
    Evidence.PRESENT: 0,
    Evidence.ABSENT: 1,
    Evidence.PARTIAL: 2,
    Evidence.EXPIRED: 3,
    Evidence.MISSING: 4,
    Evidence.AMBIGUOUS: 5,
    Evidence.UNSUPPORTED: 6,
}


def most_severe(values: Iterable[Evidence]) -> Evidence:
    """Roll several entries up without letting the worst one disappear."""
    return max(values, key=lambda value: _SEVERITY[value])


def analyze_record(
    run: dict[str, Any], checkpoints: list[dict[str, Any]]
) -> dict[str, Any]:
    """Decide what the record can and cannot support, before reading any facet."""
    expected = int(run.get("checkpoint_sequence") or 0)
    sequences = sorted(int(item.get("sequence") or 0) for item in checkpoints)
    stored = len(sequences)
    first = sequences[0] if sequences else expected + 1
    contiguous = sequences == list(range(first, first + stored))
    # Retention deletes a prefix, so a dense tail ending at the expected
    # sequence is pruned evidence. Anything else is a record we cannot trust.
    intact = contiguous and (not sequences or sequences[-1] == expected)
    pruned_through = max(0, first - 1) if intact else 0
    unsupported = {
        str(item.get("kind"))
        for item in checkpoints
        if str(item.get("kind")) not in CHECKPOINT_KINDS
    }
    unsupported |= {
        f"state:{item.get('state')}"
        for item in checkpoints
        if str(item.get("state")) not in AGENT_RUN_STATES
    }
    state = str(run.get("current_state") or "")
    if state not in AGENT_RUN_STATES:
        unsupported.add(f"state:{state}")
    if str(run.get("trigger") or "") not in RUN_TRIGGERS:
        unsupported.add(f"trigger:{run.get('trigger')}")
    holed = any(
        str(item.get("kind")) in INCOMPLETE_RECORD_KINDS for item in checkpoints
    )
    # The run row is written in the same transaction as every checkpoint, so
    # pruning step detail does not weaken what the row itself carries.
    row_settled = is_terminal(state) and intact and not unsupported and not holed
    return {
        "stored": stored,
        "expected": expected,
        "pruned_through": pruned_through,
        "damaged": not intact,
        "unsupported": tuple(sorted(unsupported)),
        "row_settled": row_settled,
        "settled": row_settled and pruned_through == 0,
    }


def absence_evidence(record: dict[str, Any], *, row_backed: bool = False) -> Evidence:
    """What silence means for one facet, given what the record can support."""
    if record["unsupported"]:
        return Evidence.UNSUPPORTED
    if record["row_settled"] if row_backed else record["settled"]:
        return Evidence.ABSENT
    if not row_backed and record["row_settled"] and record["pruned_through"]:
        return Evidence.EXPIRED
    return Evidence.MISSING


def constrain(record: dict[str, Any], observed: Evidence) -> Evidence:
    """A record this build cannot fully read cannot support a conclusion."""
    return Evidence.UNSUPPORTED if record["unsupported"] else observed


