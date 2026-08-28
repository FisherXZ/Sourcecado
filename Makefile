PYTHON ?= python3
VENV := desktop/.venv
GUI := desktop/surfaces/gui

.PHONY: setup sidecar gui native test test-python test-gui eval build doctor doctor-repair

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install -r desktop/requirements.txt
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
