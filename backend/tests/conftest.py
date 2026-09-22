import subprocess

import pytest


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
