import copy

import pytest

from coworker.agent_run_continuation import (
    merge_continuation,
    project_continuation,
)


GOOD_IDENTITY = {"message_id": "message-1", "part_id": "part-1"}


class UnsafeObject:
    def __str__(self):
        return "unsafe-object-string"


UNSAFE_TEXT_VALUES = (
    {"unsafe": "dict"},
    ["unsafe-list"],
    b"unsafe-bytes",
    UnsafeObject(),
)


def _named_continuation():
    return {
        "schema_version": 1,
        "identity": dict(GOOD_IDENTITY),
        "visible_partial": {
            "message_id": "message-1",
            "text_length": 1,
            "truncated": False,
        },
        "pending_interaction": {"kind": "approval", "id": "approval-1"},
        "pending_model": {
            "attempt_id": "model-attempt-1",
            "status": "in_flight",
            "budget_reserved": True,
        },
        "pending_tool": {
            "attempt_id": "attempt-1",
            "call_id": "call-1",
            "name": "apollo_search",
            "retry_class": "safe",
            "status": "in_flight",
            "budget_reserved": True,
        },
        "completed_tool_receipts": [
            {
                "attempt_id": "attempt-1",
                "call_id": "call-1",
                "name": "apollo_search",
                "ok": True,
                "transcript_index": 0,
                "result_sha256": "a" * 64,
            }
        ],
    }


TEXT_FIELD_CASES = (
    (("identity", "message_id"), ("identity", "message_id")),
    (("identity", "part_id"), ("identity", "part_id")),
    (("visible_partial", "message_id"), ("visible_partial", "message_id")),
    (("pending_interaction", "id"), ("pending_interaction",)),
    (("pending_model", "attempt_id"), ("pending_model",)),
    (("pending_tool", "attempt_id"), ("pending_tool",)),
    (("pending_tool", "call_id"), ("pending_tool",)),
    (("pending_tool", "name"), ("pending_tool",)),
    (
        ("completed_tool_receipts", 0, "attempt_id"),
        ("completed_tool_receipts", 0),
    ),
    (
        ("completed_tool_receipts", 0, "call_id"),
        ("completed_tool_receipts", 0),
    ),
    (
        ("completed_tool_receipts", 0, "name"),
        ("completed_tool_receipts", 0),
    ),
)


def _set_path(value, path, replacement):
    current = value
    for segment in path[:-1]:
        current = current[segment]
    current[path[-1]] = replacement


def _path_exists(value, path):
    current = value
    for segment in path:
        if isinstance(segment, int):
            if not isinstance(current, list) or len(current) <= segment:
                return False
            current = current[segment]
        else:
            if not isinstance(current, dict) or segment not in current:
                return False
            current = current[segment]
    return True


@pytest.mark.parametrize(("input_path", "output_path"), TEXT_FIELD_CASES)
@pytest.mark.parametrize("unsafe", UNSAFE_TEXT_VALUES)
def test_projector_never_stringifies_non_string_ids_or_names(
    input_path, output_path, unsafe
):
    raw = copy.deepcopy(_named_continuation())
    _set_path(raw, input_path, unsafe)

    projected = project_continuation(raw)

    assert not _path_exists(projected, output_path)
    assert "unsafe" not in repr(projected)


@pytest.mark.parametrize(
    "malformed",
    (
        None,
        {},
        {"message_id": "message-1"},
        {"part_id": "part-1"},
        {"message_id": "", "part_id": "part-1"},
        {"message_id": "message-1", "part_id": ""},
        {"message_id": {"bad": "type"}, "part_id": "part-1"},
        {"message_id": "message-1", "part_id": ["bad-type"]},
    ),
)
def test_established_identity_rejects_removal_or_malformed_replacement(malformed):
    established = merge_continuation({}, {"identity": GOOD_IDENTITY})

    with pytest.raises(ValueError):
        merge_continuation(established, {"identity": malformed})


def test_established_identity_accepts_only_an_exact_supplied_match():
    established = merge_continuation({}, {"identity": GOOD_IDENTITY})

    assert merge_continuation(established, {"identity": GOOD_IDENTITY}) == established
    with pytest.raises(ValueError):
        merge_continuation(
            established,
            {"identity": {**GOOD_IDENTITY, "message_id": "message-2"}},
        )
    with pytest.raises(ValueError):
        merge_continuation(established, {"identity": None})
    with pytest.raises(ValueError):
        merge_continuation(
            established,
            {"identity": {**GOOD_IDENTITY, "part_id": "part-2"}},
        )


@pytest.mark.parametrize("prefix", ("transcript", "event"))
def test_prefix_count_and_digest_are_required_as_an_atomic_pair(prefix):
    count_key = f"{prefix}_prefix_count"
    digest_key = f"{prefix}_prefix_sha256"
    valid_digest = "a" * 64

    invalid = (
        {count_key: 0},
        {digest_key: valid_digest},
        {count_key: -1, digest_key: valid_digest},
        {count_key: 0, digest_key: "not-a-sha256"},
        {count_key: 0, digest_key: None},
    )
    for cursor in invalid:
        with pytest.raises(ValueError):
            merge_continuation({}, {"cursor": cursor})


@pytest.mark.parametrize("prefix", ("transcript", "event"))
def test_prefix_pair_is_idempotent_and_advances_only_with_a_new_valid_pair(prefix):
    count_key = f"{prefix}_prefix_count"
    digest_key = f"{prefix}_prefix_sha256"
    first_pair = {count_key: 1, digest_key: "a" * 64}
    second_pair = {count_key: 2, digest_key: "b" * 64}
    established = merge_continuation({}, {"cursor": first_pair})

    assert merge_continuation(established, {"cursor": first_pair}) == established
    with pytest.raises(ValueError):
        merge_continuation(established, {"cursor": {count_key: 2}})
    with pytest.raises(ValueError):
        merge_continuation(
            established,
            {"cursor": {digest_key: "b" * 64}},
        )
    with pytest.raises(ValueError):
        merge_continuation(
            established,
            {"cursor": {count_key: 2, digest_key: "invalid"}},
        )
    assert merge_continuation(established, {"cursor": second_pair})["cursor"] == (
        second_pair
    )


@pytest.mark.parametrize("section", ("pending_model", "pending_tool"))
@pytest.mark.parametrize("invalid", (None, 0, 1, "true", [], {}))
def test_budget_reserved_is_strictly_boolean(section, invalid):
    raw = _named_continuation()
    raw[section]["budget_reserved"] = invalid

    projected = project_continuation(raw)

    assert section not in projected

    with pytest.raises(ValueError):
        merge_continuation({}, {section: raw[section]})


@pytest.mark.parametrize("section", ("pending_model", "pending_tool"))
def test_pending_reservation_identity_and_budget_cannot_change(section):
    initial = _named_continuation()[section]
    established = merge_continuation({}, {section: initial})

    changed_attempt = {**initial, "attempt_id": "different-attempt"}
    with pytest.raises(ValueError, match="cannot change"):
        merge_continuation(established, {section: changed_attempt})
    changed_budget = {**initial, "budget_reserved": False}
    with pytest.raises(ValueError, match="cannot change"):
        merge_continuation(established, {section: changed_budget})


def test_expected_tool_count_is_nonnegative_and_resets_only_on_a_new_step():
    first = merge_continuation(
        {},
        {
            "cursor": {
                "phase": "tools_ready",
                "step_index": 0,
                "next_tool_index": 1,
                "expected_tool_count": 2,
            }
        },
    )

    with pytest.raises(ValueError, match="expected_tool_count"):
        merge_continuation(
            first,
            {
                "cursor": {
                    "step_index": 0,
                    "next_tool_index": 1,
                    "expected_tool_count": 1,
                }
            },
        )
    advanced = merge_continuation(
        first,
        {
            "cursor": {
                "phase": "model_in_flight",
                "step_index": 1,
                "next_tool_index": 0,
                "expected_tool_count": 0,
            }
        },
    )
    assert advanced["cursor"]["expected_tool_count"] == 0
    assert advanced["cursor"]["next_tool_index"] == 0

    with pytest.raises(ValueError, match="expected_tool_count"):
        merge_continuation(
            {}, {"cursor": {"expected_tool_count": -1}}
        )
    with pytest.raises(ValueError, match="next_tool_index"):
        merge_continuation(
            {},
            {
                "cursor": {
                    "next_tool_index": 2,
                    "expected_tool_count": 1,
                }
            },
        )
