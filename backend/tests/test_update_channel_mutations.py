"""Break one guard at a time and require the test that owns it to go red.

A test suite can be green because the code is right or because the assertions
never look. This file tells those apart. Each case disables exactly one guard in
the update channel and then runs the test that is supposed to catch it, in the
normal way, and requires it to fail.

Every case is paired with a control that runs the same owning test with nothing
mutated. A mutation that "passes" because the owning test was already failing
would prove nothing, and the control is what rules that out.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import test_update_apply as apply_tests  # noqa: E402
import test_update_exercises as exercise_tests  # noqa: E402
import test_update_manifest as manifest_tests  # noqa: E402
from coworker import migrations  # noqa: E402
from coworker.update_channel import apply as apply_module  # noqa: E402
from coworker.update_channel import drain as drain_module  # noqa: E402
from coworker.update_channel import manifest as manifest_module  # noqa: E402

DIGEST_CASE = (
    "artifact_sha256",
    "f" * 64,
    manifest_module.Refusal.ARTIFACT_DIGEST_MISMATCH,
)


# --- guard 1: the manifest must not verify a digest that does not match ---


def test_control_the_digest_guard_holds(tmp_path):
    manifest_tests.test_changing_one_bound_field_refuses_with_its_own_reason(
        tmp_path, *DIGEST_CASE
    )


def test_mutant_a_manifest_that_verifies_a_wrong_digest_is_caught(
    tmp_path, monkeypatch
):
    """Defeat the digest comparison: hash every artifact to whatever was claimed."""
    monkeypatch.setattr(manifest_module, "sha256_file", lambda path: "f" * 64)
    with pytest.raises(AssertionError):
        manifest_tests.test_changing_one_bound_field_refuses_with_its_own_reason(
            tmp_path, *DIGEST_CASE
        )


# --- guard 2: an update must not proceed during an unsettled effect ------


def test_control_the_drain_gate_holds(tmp_path):
    apply_tests.test_an_update_requested_during_an_unsettled_send_does_not_proceed(
        tmp_path
    )


def test_mutant_an_update_that_proceeds_during_an_unsettled_effect_is_caught(
    tmp_path, monkeypatch
):
    """Defeat the gate entirely: report every installation ready to be replaced."""
    monkeypatch.setattr(
        drain_module,
        "assess_drain",
        lambda repo, **kwargs: drain_module.DrainAssessment(
            status=drain_module.DrainStatus.READY
        ),
    )
    with pytest.raises(AssertionError):
        apply_tests.test_an_update_requested_during_an_unsettled_send_does_not_proceed(
            tmp_path
        )


def test_mutant_a_gate_that_only_misreads_the_effect_is_still_caught(
    tmp_path, monkeypatch
):
    """A subtler break: the gate still stops, but stops for the wrong reason."""
    monkeypatch.setattr(drain_module, "unreported", lambda effects: [])
    with pytest.raises(AssertionError):
        apply_tests.test_an_update_requested_during_an_unsettled_send_does_not_proceed(
            tmp_path
        )


# --- guard 3: a rollback must actually restore ---------------------------


def test_control_the_rollback_guard_holds(tmp_path):
    exercise_tests.test_exercise_failed_update_restores_the_last_usable_version(
        tmp_path
    )


def test_mutant_a_rollback_that_leaves_the_new_bundle_in_place_is_caught(
    tmp_path, monkeypatch
):
    """Defeat the bundle half: claim the previous version was put back."""
    monkeypatch.setattr(apply_module, "restore_bundle", lambda installation: True)
    with pytest.raises(AssertionError):
        exercise_tests.test_exercise_failed_update_restores_the_last_usable_version(
            tmp_path
        )


def test_mutant_a_rollback_that_leaves_the_state_migrated_is_caught(
    tmp_path, monkeypatch
):
    """Defeat the state half: claim the backup was restored and restore nothing."""
    monkeypatch.setattr(
        migrations, "restore_backup", lambda root, backup_id: {"restored": []}
    )
    with pytest.raises(AssertionError):
        exercise_tests.test_exercise_failed_update_restores_the_last_usable_version(
            tmp_path
        )


def test_mutant_a_rollback_that_forgets_the_version_manifest_is_caught(
    tmp_path, monkeypatch
):
    """The one the migration registry itself gets wrong; see docs/update-channel.md."""
    monkeypatch.setattr(apply_module, "_restore_versions", lambda root, backup_id: None)
    with pytest.raises(AssertionError):
        exercise_tests.test_exercise_failed_update_restores_the_last_usable_version(
            tmp_path
        )
