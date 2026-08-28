# The macOS preview channel

How a new Sourcecado build replaces a running one without losing local state and
without restarting an external action that may already have happened.

## Words used here

- **Channel** — `stable` or `preview`. A property of the build you installed, not
  a setting inside it.
- **Manifest** — a small signed JSON document that describes one artifact.
- **Artifact** — the zipped `Sourcecado.app` a manifest describes.
- **Store** — one durable local file or database, as declared in
  `coworker/migrations.py`.
- **External effect** — a tool call that reaches the outside world. `gmail_send`
  is the one this design exists for.
- **Unsettled effect** — an external effect that was dispatched and has not
  reported an outcome. Nobody knows whether it happened.
- **Quarantined effect** — an unsettled effect that a restart has moved into
  review. Only a person settles it.

## How opting in works

You join the preview channel by installing a preview build. There is no toggle.

That is enforced, not just a convention. The manifest binds the channel, and the
client trusts a different signing key per channel
(`coworker/update_channel/manifest.py`, `TRUSTED_KEYS`). A stable installation
holds no key that can authenticate a preview manifest, so it refuses one with
`channel_mismatch` before it looks at anything else. The reverse holds too.

A preview build says so permanently. `PreviewChannelBadge` renders a fixed badge
in the top-right corner on every screen, and Settings carries a **Release
channel** section naming the version, the build, what is different, and what to
do when an update goes wrong. A stable build renders neither.

## The manifest

One JSON document, two halves. `signed` carries the facts; `signature` carries an
Ed25519 signature over the canonical bytes of `signed`.

```json
{
  "signed": {
    "manifest_version": 1,
    "product": "sourcecado",
    "channel": "preview",
    "version": "0.0.1.123",
    "platform": "macos",
    "arch": "aarch64",
    "artifact_name": "Sourcecado-0.0.1.123-macos-aarch64.zip",
    "artifact_size": 234995,
    "artifact_sha256": "988e24b7…",
    "minimum_upgradable_version": "0.0.1",
    "state_versions": { "people_db": 1, "conversation_db": 1, "…": 0 },
    "released_at": "2026-08-28T05:56:38Z",
    "commit": "fcb6e64aca18c836fa807ee0a009ccd2c9ca3c8a"
  },
  "signature": {
    "algorithm": "ed25519",
    "key_id": "sourcecado-preview-2026",
    "value": "BuIALj18…"
  }
}
```

What it binds, and why each one is in the signed bytes:

| Field | Binds |
| --- | --- |
| `version` | The exact version. An older or equal version is refused, so an update cannot walk backwards. |
| `channel` | Which key set governs the document, and which installations may accept it. |
| `platform`, `arch` | The machine. An `x86_64` artifact cannot install on `aarch64`. |
| `artifact_sha256`, `artifact_size` | The exact bytes. A different file cannot be described by a manifest that verifies. |
| `minimum_upgradable_version` | The oldest version this artifact can upgrade from. |
| `state_versions` | What store versions the artifact expects, checked against what this build can produce. |
| `manifest_version` | The format. A future format is refused, not partially read. |
| `product`, `artifact_name`, `released_at`, `commit` | Identity and provenance. |

### Verification fails closed

`verify_manifest` returns a `Verification`, never an exception, and every branch
either refuses or falls through to the next check. There is no default-allow
path. The checks run in this order, and the first failure wins:

1. Document shape.
2. **Closed field set.** A field this build does not know refuses the manifest.
   An ignored field is a field an attacker gets for free the day a future build
   starts reading it.
3. Field types.
4. `manifest_version`.
5. Signature algorithm.
6. Channel, which selects the key set.
7. Key id present in that channel's trust store.
8. Signature over the canonical bytes, with the domain prefix
   `sourcecado.update-manifest.v1`.
9. Product, platform, architecture.
10. Version is newer, and the upgrade path is supported.
11. `state_versions` against this build's migration registry.
12. Artifact exists, is the signed size, and hashes to the signed digest.

`TRUSTED_KEYS` is empty in this build. Sourcecado has no update signing identity
yet, so every real manifest is refused with `untrusted_key`. That is the correct
behaviour until an identity exists.
`tests/test_update_manifest.py::test_no_signing_key_is_shipped_yet_so_nothing_verifies_in_production`
goes red the day a key is added, so adding one cannot happen quietly.

## The rule that shapes everything: draining

The obvious updater stops the app, swaps the bundle, and starts it again. On a
machine that sends real mail to real people, that sequence is the bug.

If a run has dispatched a Gmail send and has not yet learned the outcome, killing
the process closes that window on "unknown" forever. Worse, a restart that
"resumes" the run could put a second copy of the same message in a real person's
inbox. `coworker/agent_run_approval.py` exists to hold that state as `ambiguous`
until a person settles it.

So `coworker/update_channel/drain.py` gates every update, and it has three
answers. Proceeding regardless is not one of them.

| State | What the update does |
| --- | --- |
| Nothing running, or every run is finished, parked on a person, or free of open effects | Proceed. Whatever is left is restart-safe: it lives in the run store and `agent_run_resume.restart()` picks it up after the new build launches. |
| A process holds a live lease on a run | **Wait.** It clears on its own. If the wait runs out, refuse. |
| An effect is dispatched and has not reported back | **Wait.** The dispatching process may still report. If the wait runs out, refuse. |
| An effect is quarantined | **Refuse now.** Waiting cannot settle it; only a person can. |

The second half of the contract is what the gate does *not* do. **It performs no
writes at all.** It does not quarantine an unreported effect — that is
`agent_run_resume.restart()`'s decision after a crash actually happened — and it
does not resolve a quarantine, which is a person's decision and carries their
name. An updater that tidies the run store on the way past is exactly how a
machine records an outcome nobody observed.

The criterion has two halves: wait for active work to drain, **or** record
restart-safe continuation. The second half is `UpdateOutcome.continuable`, which
lists the runs that were open when the update ran and are safe to pick up after
it — parked on a person, or free of any open external effect. The record is
declarative: the work itself is already durable in the run store, and
`agent_run_resume.restart()` classifies it on the next launch. The update does
not resume anything itself.
(`tests/test_update_apply.py::test_an_update_records_the_work_that_will_continue_after_the_restart`)

`tests/test_update_apply.py::test_an_update_requested_during_an_unsettled_send_does_not_proceed`
is the test that proves the refusing half. It asserts the update reached the drain stage first
(so the assertion is not passing because nothing started), then asserts the
bundle is untouched, the effect row is byte-identical, no checkpoint was
appended, and the quarantine queue is still empty.

## State compatibility and the backup

Neither is re-implemented here. `coworker/migrations.py` is the one registry of
store versions, backups, and rollback, and the updater calls it:

- `plan_migrations` decides compatibility. A store at an unknown future version,
  an unreadable store, or a store with no registered path forward blocks the
  whole update — the registry's own fail-closed rule, not a second opinion about
  it.
- `create_backup` takes the backup, covering every registered store, before the
  first change. Only taken when the plan has pending work, because an update with
  no migration changes no state and has nothing to restore.
- `apply_migrations` applies it, with the same backup handed in so a single
  update does not write the same files twice.
- `restore_backup` puts it back.

### The stage order, and why

```
verify → drain → compatibility → stage → backup → migrate → activate → health
```

Everything that can fail without consequence runs first. Verification, the drain
gate, the compatibility read, and unpacking the archive all change nothing, so a
failure in any of them leaves the installation exactly as it was.

Migrating before activating is deliberate. The registry that runs is *this*
build's, so the state it produces is state this build already understands: a
crash between the migration and the bundle swap leaves a consistent system, and a
failure at the migration step leaves the old bundle untouched with only the state
to restore. The reverse order opens a window where a new bundle sits over state
it has not migrated.

That order has a consequence worth stating plainly: **a release that raises a
store version cannot be installed by this path.** The updater running is the old
build, and it can only reach the versions its own registry knows. The manifest's
`state_versions` is what enforces it — an artifact declaring a version this build
cannot produce is refused with `state_ahead_of_migrator` rather than installed
over state it would strand. In practice that means the migration ships in the
release *before* the one that needs it. The alternative, handing the migration to
the new build's first launch, needs a launch-time migration hook in `server.py`
and is not in this change.

### A gap in the registry, compensated for here

`migrations.restore_backup` restores store contents but not `state_versions.json`,
which is where JSONL logs, opaque files, and config documents record their
version. Measured on this branch: after backup, migrate, and restore of a legacy
state directory, five stores read back at version 1 with version 0 contents.

Today that is harmless, because those stores' `0 → 1` steps only record a version
and change no content. It will not stay harmless. The updater therefore snapshots
`state_versions.json` into the backup directory before it migrates and restores it
on rollback, including restoring the fact that the file did not exist yet.
`tests/test_update_channel_mutations.py::test_mutant_a_rollback_that_forgets_the_version_manifest_is_caught`
holds that in place. **The registry itself should do this**; the fix belongs in
`coworker/migrations.py` and was outside this change's write boundary.

## When an update fails

Every failure keeps or restores the last usable version, and says what happened
in plain words.

| Failure | Result |
| --- | --- |
| Manifest does not verify | `refused`. Nothing touched. |
| Work still in flight | `blocked`. Nothing touched, nothing recorded. |
| Local state is incompatible | `refused`. Nothing touched. Doctor's exact reason is quoted. |
| Archive is corrupt or the wrong shape | `refused`. Nothing touched, no backup written. |
| Backup cannot be written | `refused`. Nothing touched. |
| Migration fails | `rolled_back`. State restored from the backup, old bundle never moved. |
| Bundle cannot be swapped | `rolled_back`. Both restored. |
| New version does not start, previous version exists | `rolled_back`. Both restored. |
| New version does not start, nothing was installed | `failed`. Failed bundle removed. There is no previous version to put back. |

The launch check runs the sidecar inside the newly installed application on
loopback with its own token and a **throwaway** state directory, and waits for
`/v1/health`. A launch check that opened the operator's real state would be a
change the rollback could not undo.

### Rolling back by hand

The previous version stays on disk as `Sourcecado.app.previous` beside the
current one, and the backup taken before the update stays under
`<state>/backups/<backup_id>/`.

```sh
# Quit Sourcecado first. Rolling back while it is running is the one thing
# that can lose work.
make update-rollback BUNDLE=/Applications/Sourcecado.app BACKUP=<backup_id>
```

`make update-rollback` without `BACKUP` restores only the application, which is
correct when the failed update never reached the migration step. Sourcecado
Doctor lists the backups: `make doctor`.

## The five exercises

`tests/test_update_exercises.py` runs them. The names below are the test names,
so the prose and the code cannot drift.

| Exercise | Test | What it proves |
| --- | --- | --- |
| Clean install | `test_exercise_clean_install` | No previous application and no previous state. Installs, takes no backup, migrates nothing. |
| Upgrade from the prior preview | `test_exercise_upgrade_from_the_prior_preview` | State written by the previous build's schema comes forward. Backup taken, `people_db` migrated, nothing pending afterwards, previous bundle kept. |
| Failed update | `test_exercise_failed_update_restores_the_last_usable_version` | The launch check fails. Bundle back at the old version, staging cleared, every store version back where it started, and Doctor does not disagree. |
| Rollback | `test_exercise_rollback_after_a_successful_update` | A clean update is deliberately reversed. Bundle and every store restored. |
| Preserved person file | `test_exercise_the_person_file_reads_back_through_its_own_store` and `test_exercise_the_person_file_survives_a_failed_update_and_a_rollback` | The person file opens through `PersonStore` after the upgrade, with name, company, email, sequence state, and timeline intact — and survives a failed update and a rollback unchanged. |

The person-file exercise is the one a user would notice. A failed update is
visible; a lost person file is not.

## Credentials, and the four surfaces they could leak through

`coworker/bundle_redaction.py` is the only matcher used. A second matcher drifts,
and the one that drifts is the one that misses. `tests/test_update_secrets.py`
plants a credential on each surface and proves it did not arrive.

| Surface | Guard |
| --- | --- |
| **Manifest** | `packaging/update_manifest.py generate` scans the assembled manifest and refuses to write on a match. This is the new surface and the most likely to leak, because it is built from build metadata. The refusal names the category and location, never the value. |
| **Artifact** | Files shipped beside the artifact (`provenance.txt`, `checksums.txt`) are passed with `--attest` and scanned by the same pass. A match stops the build with no manifest written. |
| **Logs** | Operator-facing text is built as a fixed sentence plus a variable half. The variable half — an exception string, a registry error — goes through `safe_text` on its own, so a credential there is withheld in place and the sentence survives. `_outcome` scans the composed sentence again, and withholds all of it if a call site forgot. |
| **Diagnostic bundles** | `UpdateOutcome.to_dict()` is asserted clean under `bundle_redaction.scan` with planted canaries, which is the same check the bundle fails closed on. |

The signing key is read from an environment variable, never written to the
manifest, and never repeated in an error. The registered scan reads the build's
own environment, so pasting the signing key into a commit message is caught as
`registered_secret`.

## Signing and notarization: what is authored and what is unrun

The CI steps are written in `.github/workflows/ci.yml` in the `macos-preview`
job. **None of them have run.** They are guarded on the signing secret being
present and are skipped with an explicit notice when it is not, so a run without
credentials cannot be mistaken for a signed one.

To turn them on, supply these as protected repository secrets and variables:

| Name | Kind | What it is |
| --- | --- | --- |
| `MACOS_CERTIFICATE_P12` | secret | Base64 of the Developer ID Application `.p12`. |
| `MACOS_CERTIFICATE_PASSWORD` | secret | Password for that `.p12`. |
| `MACOS_SIGNING_IDENTITY` | secret | e.g. `Developer ID Application: NAME (TEAMID)`. |
| `APPLE_NOTARY_ISSUER_ID` | secret | App Store Connect API issuer id. |
| `APPLE_NOTARY_KEY_ID` | secret | App Store Connect API key id. |
| `APPLE_NOTARY_PRIVATE_KEY` | secret | The `.p8` contents. |
| `SOURCECADO_UPDATE_SIGNING_KEY` | secret | Base64 of a 32-byte Ed25519 seed. Generate it offline, never in CI. |
| `SOURCECADO_UPDATE_KEY_ID` | variable | The key id written into every manifest. |
| `SOURCECADO_UPDATE_PUBLIC_KEY` | variable | Base64 of the matching public key, used by the in-CI verification step. |

Then add the public key to `TRUSTED_KEYS` in
`coworker/update_channel/manifest.py` under the `preview` channel and update the
tripwire test. Until that happens, no manifest verifies on any machine.

Generate the update signing key offline:

```sh
python3 -c "
import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
k = Ed25519PrivateKey.generate()
seed = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
print('secret SOURCECADO_UPDATE_SIGNING_KEY =', base64.b64encode(seed).decode())
print('variable SOURCECADO_UPDATE_PUBLIC_KEY =', base64.b64encode(pub).decode())
"
```

## Preview version numbers

The preview channel stamps `<package.json version>.<CI run number>`, for example
`0.0.1.123`. Four dotted components parse, and the run number rises on every
build, so consecutive preview builds are always an upgrade of each other without
anyone hand-bumping a version. `minimum_upgradable_version` stays at the
package.json version.

## Local commands

```sh
make test-update              # the whole update channel suite, exercises and mutations
make update-manifest ARTIFACT=… VERSION=… KEY_ID=…    # needs SOURCECADO_UPDATE_SIGNING_KEY
make update-verify ARTIFACT=… MANIFEST=… PUBLIC_KEY=…
make update-rollback BUNDLE=… [BACKUP=…]
```

## Known gaps

- **Nothing is signed or notarized yet.** The steps are written and unrun. See
  the table above.
- **No download client.** `apply_update` takes an artifact that is already on
  disk. Fetching it, and where the manifest is published, are not in this change.
- **No in-app update trigger.** The GUI shows the channel and the rollback path;
  it does not start an update. Wiring one needs an endpoint in `server.py`, which
  was outside this change's write boundary.
- **A release that raises a store version cannot be installed by this path.** See
  the stage-order section.
- **`migrations.restore_backup` does not restore `state_versions.json`.**
  Compensated for here; the fix belongs in the registry.
- **Verified on `aarch64-apple-darwin` only**, and the manifest generation and
  verification runs recorded on this branch used a stand-in artifact, because no
  Tauri release build exists in this worktree.
