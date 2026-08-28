PYTHON ?= python3
VENV := desktop/.venv
GUI := desktop/surfaces/gui

.PHONY: setup sidecar gui native test test-python test-gui eval eval-sourcing build doctor doctor-repair secret-scan build-sidecar smoke-test

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

# Confirms a registered secret's absence from local state without ever
# printing it. Pass KEY=<name> to scan for one value. After rotation, pass
# REVOKED_FROM=<pre-rotation-secrets.json> so the scan searches the revoked
# value rather than the replacement now in the live vault, e.g.
# `make secret-scan KEY=apollo REVOKED_FROM=./pre-rotation-secrets.json`.
secret-scan:
	cd desktop && .venv/bin/python -m coworker.secret_scan $(if $(KEY),--secret-key $(KEY),) $(if $(REVOKED_FROM),--revoked-from $(REVOKED_FROM),)

build:
	npm --prefix $(GUI) run build

build-sidecar:
	bash desktop/packaging/build_sidecar.sh

smoke-test:
	$(PYTHON) desktop/packaging/smoke_test.py desktop/surfaces/gui/src-tauri/resources/sourcecado-sidecar/sourcecado-sidecar
