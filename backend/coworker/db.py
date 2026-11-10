"""Postgres access for the hosted service.

Every query runs inside `Database.tx(user_id)`. It opens one transaction and
sets `app.user_id` for that transaction only. Row-level security policies read
that setting, so a query sees only the rows of that user.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg_pool import ConnectionPool

DATABASE_URL_ENV = "DATABASE_URL"

# The one user that local development runs as until sign-in exists (migration
# slice S13). `supabase/seed.sql` creates the matching row.
DEV_USER_ID = UUID("00000000-0000-4000-8000-000000000001")


class Database:
    def __init__(self, url: str, *, max_size: int = 10) -> None:
        self._pool = ConnectionPool(url, min_size=1, max_size=max_size, open=True)

    @contextmanager
    def tx(self, user_id: UUID | str) -> Iterator[psycopg.Connection]:
        """One transaction that acts as `user_id`.

        Commits when the block ends. Rolls back when the block raises.
        """
        acting_as = user_id if isinstance(user_id, UUID) else UUID(user_id)
        with self._pool.connection() as conn:
            conn.execute(
                "select set_config('app.user_id', %s, true)", (str(acting_as),)
            )
            yield conn

    def close(self) -> None:
        self._pool.close()


# --- Phase 1 seam -----------------------------------------------------------
# A store still takes the state directory that it took with SQLite, and asks
# `resolve()` which database and which user that directory means. The service
# answers "the one database, the development user". Tests and evals answer "a
# new user for each directory", so two directories stay two isolated stores.
# Slice S15 removes this: each request then names its user.

Binding = tuple[Database, UUID]

_resolver: Callable[[Path], Binding] | None = None
_default: Database | None = None
_default_lock = threading.Lock()


def resolve(base_dir: str | Path) -> Binding:
    """The database and the user for a store that was opened at `base_dir`."""
    if _resolver is not None:
        return _resolver(Path(base_dir))
    return _default_database(), DEV_USER_ID


def set_resolver(resolver: Callable[[Path], Binding] | None) -> None:
    global _resolver
    _resolver = resolver


def _default_database() -> Database:
    global _default
    with _default_lock:
        if _default is None:
            url = os.environ.get(DATABASE_URL_ENV, "").strip()
            if not url:
                raise RuntimeError(
                    f"{DATABASE_URL_ENV} is not set. For local development, run "
                    "`make db-up` and start the backend with `make backend`."
                )
            _default = Database(url)
        return _default
