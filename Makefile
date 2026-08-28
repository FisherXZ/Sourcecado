PYTHON ?= python3
VENV := desktop/.venv
GUI := desktop/surfaces/gui

.PHONY: setup sidecar gui native test test-python test-gui eval eval-sourcing build doctor doctor-repair build-sidecar smoke-test test-update update-status update-rollback update-manifest update-verify

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --require-hashes -r desktop/requirements.lock
	npm --prefix $(GUI) ci

sidecar:
	cd desktop && .venv/bin/python -m coworker.run

gui:
	npm --prefix $(GUI) run dev

native:
	npm --prefix $(GUI) run tauri:dev

test: test-python test-gui

test-python:
	cd desktop && .venv/bin/pytest -q

test-gui:
	npm --prefix $(GUI) test

eval:
	cd desktop && .venv/bin/python -m coworker.evals --artifacts .eval-artifacts

eval-sourcing:
	cd desktop && .venv/bin/python -m coworker.evals --suite sourcing --artifacts .eval-artifacts

doctor:
	cd desktop && .venv/bin/python -m coworker.doctor

doctor-repair:
	cd desktop && .venv/bin/python -m coworker.doctor repair

build:
	npm --prefix $(GUI) run build

build-sidecar:
	bash desktop/packaging/build_sidecar.sh

smoke-test:
	$(PYTHON) desktop/packaging/smoke_test.py desktop/surfaces/gui/src-tauri/resources/sourcecado-sidecar/sourcecado-sidecar

# --- preview update channel -------------------------------------------------
# desktop/docs/update-channel.md explains every target below.

test-update:
	cd desktop && .venv/bin/pytest -q tests/test_update_manifest.py tests/test_update_drain.py \
		tests/test_update_apply.py tests/test_update_exercises.py \
		tests/test_update_secrets.py tests/test_update_health.py \
		tests/test_update_channel_mutations.py

# Is anything in flight that an update would interrupt? Reads only.
update-status:
	cd desktop && .venv/bin/python -m coworker.update_channel status

# BUNDLE is the installed app. BACKUP is optional: omit it when the failed
# update never reached the migration step. Quit Sourcecado first.
update-rollback:
	cd desktop && .venv/bin/python -m coworker.update_channel rollback \
		--bundle "$(BUNDLE)" $(if $(BACKUP),--backup "$(BACKUP)",) $(if $(LIST),--list-backups,)

# Needs SOURCECADO_UPDATE_SIGNING_KEY in the environment. Refuses to write a
# manifest whose build metadata matches the credential scan.
update-manifest:
	cd desktop && .venv/bin/python packaging/update_manifest.py generate \
		--artifact "$(ARTIFACT)" --output "$(MANIFEST)" --version "$(VERSION)" \
		--minimum-from "$(MINIMUM_FROM)" --key-id "$(KEY_ID)" \
		--commit "$$(git rev-parse HEAD)" \
		--released-at "$$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Needs only the public key, the same one every installation would hold.
update-verify:
	cd desktop && .venv/bin/python packaging/update_manifest.py verify \
		--manifest "$(MANIFEST)" --artifact "$(ARTIFACT)" \
		--installed-version "$(INSTALLED_VERSION)" \
		--trust "$(KEY_ID)=$(PUBLIC_KEY)"
