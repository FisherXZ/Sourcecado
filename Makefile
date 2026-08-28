PYTHON ?= python3
VENV := desktop/.venv
GUI := desktop/surfaces/gui

.PHONY: setup sidecar gui native test test-python test-gui eval build doctor doctor-repair build-sidecar smoke-test

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
