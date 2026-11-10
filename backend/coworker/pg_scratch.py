"""Scratch Postgres databases for tests and evals.

The running service does not use this module. It creates a database, applies
the migrations, and drops the database at the end.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from coworker.db import Binding, Database

ADMIN_URL_ENV = "TEST_DATABASE_URL"
# The local Supabase database that `make db-up` starts. The port is set in
# supabase/config.toml. These are the public defaults of the Supabase CLI.
DEFAULT_ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:53322/postgres"

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
# Supabase creates the real auth.users table. A scratch database needs a table
# with the same key, because the migrations have foreign keys to it.
AUTH_STUB = Path(__file__).with_name("pg_scratch_auth_stub.sql")


def admin_url() -> str:
    return os.environ.get(ADMIN_URL_ENV) or DEFAULT_ADMIN_URL


def apply_sql_dir(conn: psycopg.Connection, directory: Path) -> list[str]:
    """Run each .sql file in name order. Return the names that ran."""
    ran: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        conn.execute(path.read_text(encoding="utf-8"))
        ran.append(path.name)
    return ran


@contextmanager
def scratch_database(url: str) -> Iterator[str]:
    """Create a migrated database, yield its connection string, then drop it.

    Raises psycopg.OperationalError when the server is not reachable.
    """
    admin = psycopg.connect(url, autocommit=True, connect_timeout=5)
    dbname = f"sourcecado_scratch_{os.getpid()}_{uuid4().hex[:8]}"
    name = sql.Identifier(dbname)
    admin.execute(sql.SQL("create database {} template template0").format(name))
    try:
        scratch_url = make_conninfo(url, dbname=dbname)
        with psycopg.connect(scratch_url, autocommit=True) as conn:
            conn.execute(AUTH_STUB.read_text(encoding="utf-8"))
            apply_sql_dir(conn, MIGRATIONS_DIR)
        yield scratch_url
    finally:
        admin.execute(sql.SQL("drop database if exists {} with (force)").format(name))
        admin.close()


def empty(url: str) -> None:
    """Remove every row. Keep the tables."""
    with psycopg.connect(url, autocommit=True) as conn:
        rows = conn.execute(
            "select schemaname, tablename from pg_tables where schemaname = 'public'"
        ).fetchall()
        targets = [sql.Identifier(schema, table) for schema, table in rows]
        targets.append(sql.Identifier("auth", "users"))
        conn.execute(
            sql.SQL("truncate {} restart identity cascade").format(
                sql.SQL(", ").join(targets)
            )
        )


def create_user(url: str) -> UUID:
    user_id = uuid4()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "insert into auth.users (id, email) values (%s, %s)",
            (user_id, f"{user_id}@example.test"),
        )
    return user_id


def state_dir_resolver(database: Database, url: str) -> Callable[[Path], Binding]:
    """A resolver that gives each state directory its own user.

    The same directory always gets the same user, so a store that is opened
    again at that directory sees its earlier rows.
    """
    users: dict[Path, UUID] = {}
    lock = threading.Lock()

    def resolver(base_dir: Path) -> Binding:
        key = Path(base_dir).expanduser().resolve()
        with lock:
            if key not in users:
                users[key] = create_user(url)
            return database, users[key]

    return resolver
