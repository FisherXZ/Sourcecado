"""Postgres test fixtures.

One scratch database for each test session. It is emptied after each test.
Inside one test, the unit of isolation is a user: call `make_user()` two times
to get two separate owners.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from coworker.db import Database

TEST_DATABASE_URL_ENV = "TEST_DATABASE_URL"
# The local Supabase database that `make db-up` starts. The port is set in
# supabase/config.toml. These are the public defaults of the Supabase CLI.
DEFAULT_ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:53322/postgres"

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
AUTH_STUB = Path(__file__).with_name("auth_stub.sql")


def admin_url() -> str:
    return os.environ.get(TEST_DATABASE_URL_ENV) or DEFAULT_ADMIN_URL


def connect_admin(url: str) -> psycopg.Connection:
    """Connect, or fail the run. A missing database must never skip tests."""
    try:
        return psycopg.connect(url, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError:
        pytest.fail(
            "Postgres is not reachable. Run `make db-up`, or set "
            f"{TEST_DATABASE_URL_ENV}.",
            pytrace=False,
        )


def apply_sql_dir(conn: psycopg.Connection, directory: Path) -> list[str]:
    """Run each .sql file in name order. Return the names that ran."""
    ran: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        conn.execute(path.read_text(encoding="utf-8"))
        ran.append(path.name)
    return ran


def _empty(url: str) -> None:
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


@pytest.fixture(scope="session")
def pg_database() -> Iterator[str]:
    """Connection string of a new database with the auth stub and all migrations."""
    admin = connect_admin(admin_url())
    dbname = f"sourcecado_test_{os.getpid()}"
    name = sql.Identifier(dbname)
    admin.execute(sql.SQL("drop database if exists {} with (force)").format(name))
    admin.execute(sql.SQL("create database {} template template0").format(name))
    url = make_conninfo(admin_url(), dbname=dbname)
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(AUTH_STUB.read_text(encoding="utf-8"))
            apply_sql_dir(conn, MIGRATIONS_DIR)
        yield url
    finally:
        admin.execute(sql.SQL("drop database if exists {} with (force)").format(name))
        admin.close()


@pytest.fixture
def db(pg_database: str) -> Iterator[Database]:
    database = Database(pg_database, max_size=2)
    try:
        yield database
    finally:
        database.close()
        _empty(pg_database)


@pytest.fixture
def make_user(pg_database: str, db: Database) -> Callable[[], UUID]:
    """Return a function that inserts one auth.users row and returns its id."""

    def create() -> UUID:
        user_id = uuid4()
        with psycopg.connect(pg_database, autocommit=True) as conn:
            conn.execute(
                "insert into auth.users (id, email) values (%s, %s)",
                (user_id, f"{user_id}@example.test"),
            )
        return user_id

    return create
