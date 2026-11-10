import subprocess

import pytest

from tests.pg_fixtures import (  # noqa: F401  (fixtures)
    _session_database,
    db,
    make_user,
    pg_database,
    stores_use_the_test_database,
)


@pytest.fixture
def eval_children_use_the_test_database(pg_database, monkeypatch):
    """Spawned eval children open their stores in the test database too."""
    from coworker.evals.runner import EVAL_DATABASE_URL_ENV

    monkeypatch.setenv(EVAL_DATABASE_URL_ENV, pg_database)


@pytest.fixture(autouse=True)
def forbid_host_browser_launches(monkeypatch):
    real_popen = subprocess.Popen

    def guarded_popen(command, *args, **kwargs):
        if isinstance(command, (list, tuple)) and command and command[0] == "open":
            pytest.fail(
                "desktop tests must inject a browser_opener instead of opening the host browser"
            )
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
