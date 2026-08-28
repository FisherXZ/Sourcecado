"""The Sourcecado macOS preview channel: how a new build replaces a running one.

Three modules, in the order an update passes through them.

- `manifest` authenticates. Nothing installs without a signature over a closed
  field set that binds the exact version, channel, platform, architecture, and
  artifact digest.
- `drain` decides whether now is a safe moment. It is the module that exists
  because the obvious updater -- stop, swap, start -- is wrong here. A run that
  has dispatched a Gmail send and does not yet know the outcome must not be
  killed, because killing it turns a knowable outcome into a permanently
  ambiguous one, and a restart that "resumes" it could send a second real email
  to a real person. See `coworker/agent_run_approval.py` for the fence that
  holds an ambiguous effect, and note that this module never writes to it: an
  update neither creates a quarantine nor settles one.
- `apply` sequences the rest: state compatibility through the migration
  registry, the backup that registry requires, the bundle swap, the health
  check, and the rollback that puts the last usable version back.

State compatibility is not re-implemented here. `coworker/migrations.py` is the
one registry of store versions, backups, and rollback, and this package calls
it. A second compatibility check would be a second opinion, and the one that
drifts is the one that is wrong.
"""

from coworker.update_channel.apply import (
    Installation,
    UpdateOutcome,
    UpdateStage,
    UpdateStatus,
    apply_update,
    rollback,
)
from coworker.update_channel.drain import (
    DrainAssessment,
    DrainBlocker,
    DrainStatus,
    assess_drain,
    wait_for_drain,
)
from coworker.update_channel.manifest import (
    MANIFEST_VERSION,
    SIGNED_FIELDS,
    TRUSTED_KEYS,
    BoundManifest,
    BuildIdentity,
    Channel,
    Refusal,
    Verification,
    build_manifest,
    registry_state_versions,
    sha256_file,
    sign_manifest,
    verify_manifest,
)
from coworker.update_channel.redaction import registered_secrets, safe_text

__all__ = [
    "MANIFEST_VERSION",
    "SIGNED_FIELDS",
    "TRUSTED_KEYS",
    "BoundManifest",
    "BuildIdentity",
    "Channel",
    "DrainAssessment",
    "DrainBlocker",
    "DrainStatus",
    "Installation",
    "Refusal",
    "UpdateOutcome",
    "UpdateStage",
    "UpdateStatus",
    "Verification",
    "apply_update",
    "assess_drain",
    "build_manifest",
    "registered_secrets",
    "registry_state_versions",
    "rollback",
    "safe_text",
    "sha256_file",
    "sign_manifest",
    "verify_manifest",
    "wait_for_drain",
]
