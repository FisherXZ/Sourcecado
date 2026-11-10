"""Postgres access for the hosted service.

Every query runs inside `Database.tx(user_id)`. It opens one transaction and
sets `app.user_id` for that transaction only. Row-level security policies read
that setting, so a query sees only the rows of that user.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import psycopg
from psycopg_pool import ConnectionPool

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
