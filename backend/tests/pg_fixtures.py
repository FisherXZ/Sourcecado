"""Postgres test fixtures.

One scratch database for each test session. It is emptied after each test.
The unit of isolation is a user. A store that is opened at a state directory
gets the user of that directory, so two directories are two isolated stores.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from uuid import UUID

import psycopg
import pytest

from coworker import db as db_module
from coworker.db import Database
from coworker.pg_scratch import (
    ADMIN_URL_ENV,
    admin_url,
    create_user,
    empty,
    scratch_database,
    state_dir_resolver,
)


def open_scratch_database(url: str):
    """Enter a scratch database, or fail the run. A missing database must never skip tests."""
    scratch = scratch_database(url)
    try:
        return scratch, scratch.__enter__()
    except psycopg.OperationalError:
        pytest.fail(
            f"Postgres is not reachable. Run `make db-up`, or set {ADMIN_URL_ENV}.",
            pytrace=False,
        )


@pytest.fixture(scope="session")
def pg_database() -> Iterator[str]:
    """Connection string of a new database with all migrations applied."""
    scratch, url = open_scratch_database(admin_url())
    try:
        yield url
    finally:
        scratch.__exit__(None, None, None)


@pytest.fixture(scope="session")
def _session_database(pg_database: str) -> Iterator[Database]:
    # One pool for the whole run. A new pool for each test costs about half a
    # second to close, which is too slow for a suite of this size.
    database = Database(pg_database, max_size=8)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def db(pg_database: str, _session_database: Database) -> Iterator[Database]:
    try:
        yield _session_database
    finally:
        empty(pg_database)


@pytest.fixture
def make_user(pg_database: str, db: Database) -> Callable[[], UUID]:
    """Return a function that inserts one auth.users row and returns its id."""
    return lambda: create_user(pg_database)


@pytest.fixture(autouse=True)
def stores_use_the_test_database(request: pytest.FixtureRequest) -> Iterator[None]:
    """Point `coworker.db.resolve` at the scratch database for this test.

    Nothing connects until a store asks. A test that opens no store does not
    need Postgres.
    """
    bound: dict = {}

    def resolver(base_dir):
        if not bound:
            url = request.getfixturevalue("pg_database")
            database = request.getfixturevalue("_session_database")
            bound.update(url=url, resolve=state_dir_resolver(database, url))
        return bound["resolve"](base_dir)

    db_module.set_resolver(resolver)
    try:
        yield
    finally:
        db_module.set_resolver(None)
        if bound:
            empty(bound["url"])
