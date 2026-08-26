PYTHON ?= python3
VENV := desktop/.venv
GUI := desktop/surfaces/gui

.PHONY: setup sidecar gui native test test-python test-gui build

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

build:
	npm --prefix $(GUI) run build
