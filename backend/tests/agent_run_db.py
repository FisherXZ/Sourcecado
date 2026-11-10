"""Test helpers for the Agent Run store on Postgres.

The tests used to open `agent_runs.db` with `sqlite3` to plant a broken row or
to scan the stored bytes. These helpers do the same against the repository's
own database and user.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg

from coworker import db
from coworker.agent_run_repository import AgentRunRepository

TABLES = ("agent_runs", "agent_run_checkpoints", "agent_run_effects")


@contextmanager
def raw(repo: AgentRunRepository) -> Iterator[psycopg.Connection]:
    """One transaction on the repository's database, as its user.

    A raw edit must still name `user_id` in its WHERE clause.
    """
    with repo._db.tx(repo._user_id) as conn:
        yield conn


def stored_text(repo: AgentRunRepository) -> bytes:
    """Every stored value of this user's runs, checkpoints, and effects."""
    parts: list[str] = []
    with raw(repo) as conn:
        for table in TABLES:
            for row in conn.execute(
                f"SELECT * FROM {table} WHERE user_id = %s", (repo._user_id,)
            ):
                parts.extend("" if value is None else str(value) for value in row)
    return "\n".join(parts).encode()


# A child process has no test resolver. This preamble binds every state
# directory in the child to the parent's database and to the user of the
# parent's directory. Put it after the child's `sys.path.insert`.
CHILD_PREAMBLE = """
import os as _os
from uuid import UUID as _UUID
from coworker import db as _db
from coworker.db import Database as _Database
_binding = (
    _Database(_os.environ["AGENT_RUN_TEST_DATABASE_URL"], max_size=2),
    _UUID(_os.environ["AGENT_RUN_TEST_USER_ID"]),
)
_db.set_resolver(lambda _path: _binding)
"""


def child_env(base_dir: str | Path) -> dict[str, str]:
    """Environment for a child process that opens the store at `base_dir`."""
    database, user_id = db.resolve(Path(base_dir))
    return {
        **os.environ,
        "AGENT_RUN_TEST_DATABASE_URL": database._pool.conninfo,
        "AGENT_RUN_TEST_USER_ID": str(user_id),
    }
