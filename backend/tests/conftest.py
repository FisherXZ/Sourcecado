import subprocess

import pytest

from tests.pg_fixtures import (  # noqa: F401  (fixtures)
    _session_database,
    db,
    make_user,
    pg_database,
    stores_use_the_test_database,
)


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
