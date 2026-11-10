"""The Postgres foundation: `Database.tx`, the test fixtures, and the seed."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from coworker import db as db_module
from coworker.db import DEV_USER_ID, Database, resolve
from coworker.pg_scratch import REPO_ROOT, apply_sql_dir
from tests.pg_fixtures import open_scratch_database

SETTING = "select current_setting('app.user_id', true)"


@pytest.fixture(scope="module")
def scratch_table(pg_database):
    """One user-owned table, so the tests can write real rows."""
    with psycopg.connect(pg_database, autocommit=True) as conn:
        conn.execute(
            "create table public.scratch_kv ("
            " user_id uuid not null references auth.users(id) on delete cascade,"
            " k text not null, v text not null, primary key (user_id, k))"
        )
    yield
    with psycopg.connect(pg_database, autocommit=True) as conn:
        conn.execute("drop table public.scratch_kv")


def _rows(pg_database: str) -> list[tuple]:
    with psycopg.connect(pg_database, autocommit=True) as conn:
        return conn.execute("select user_id, k, v from public.scratch_kv").fetchall()


def test_tx_sets_the_acting_user(db, make_user):
    user = make_user()
    with db.tx(user) as conn:
        assert conn.execute(SETTING).fetchone()[0] == str(user)


def test_tx_accepts_the_user_id_as_text(db, make_user):
    user = make_user()
    with db.tx(str(user)) as conn:
        assert conn.execute(SETTING).fetchone()[0] == str(user)


def test_tx_refuses_a_value_that_is_not_a_uuid(db):
    with pytest.raises(ValueError):
        with db.tx("not-a-user"):
            pass


def test_the_acting_user_does_not_outlive_the_transaction(pg_database, make_user):
    # One connection in the pool, so the second block reuses the first one's
    # connection. A setting that leaked would still be visible there.
    single = Database(pg_database, max_size=1)
    try:
        first, second = make_user(), make_user()
        with single.tx(first) as conn:
            backend = conn.execute("select pg_backend_pid()").fetchone()[0]
        with single.tx(second) as conn:
            assert conn.execute("select pg_backend_pid()").fetchone()[0] == backend
            assert conn.execute(SETTING).fetchone()[0] == str(second)
        with single._pool.connection() as conn:
            assert conn.execute("select pg_backend_pid()").fetchone()[0] == backend
            assert conn.execute(SETTING).fetchone()[0] in (None, "")
    finally:
        single.close()


def test_tx_commits_when_the_block_ends(db, make_user, pg_database, scratch_table):
    user = make_user()
    with db.tx(user) as conn:
        conn.execute(
            "insert into public.scratch_kv values (%s, 'k', 'kept')", (user,)
        )
    assert _rows(pg_database) == [(user, "k", "kept")]


def test_tx_rolls_back_when_the_block_raises(db, make_user, pg_database, scratch_table):
    user = make_user()
    with pytest.raises(RuntimeError):
        with db.tx(user) as conn:
            conn.execute(
                "insert into public.scratch_kv values (%s, 'k', 'lost')", (user,)
            )
            raise RuntimeError("stop")
    assert _rows(pg_database) == []


# The next two tests write the same key. Each one first asserts that the table
# is empty, so each fails if the other's row is still there, in either order.
@pytest.mark.parametrize("label", ["first", "second"])
def test_each_test_starts_with_an_empty_database(
    label, db, make_user, pg_database, scratch_table
):
    assert _rows(pg_database) == []
    with psycopg.connect(pg_database, autocommit=True) as conn:
        assert conn.execute("select count(*) from auth.users").fetchone()[0] == 0
    user = make_user()
    with db.tx(user) as conn:
        conn.execute(
            "insert into public.scratch_kv values (%s, 'same', %s)", (user, label)
        )
    assert len(_rows(pg_database)) == 1


def test_make_user_gives_two_owners_in_one_test(db, make_user, pg_database, scratch_table):
    honest, broken = make_user(), make_user()
    assert honest != broken
    for user, value in ((honest, "a"), (broken, "b")):
        with db.tx(user) as conn:
            conn.execute(
                "insert into public.scratch_kv values (%s, 'same', %s)", (user, value)
            )
    assert sorted(_rows(pg_database)) == sorted(
        [(honest, "same", "a"), (broken, "same", "b")]
    )


def test_sql_files_run_in_name_order(pg_database, tmp_path: Path):
    # Written in the wrong order on purpose. The second file needs the first.
    (tmp_path / "0002_rows.sql").write_text(
        "insert into public.scratch_order values (1); "
        "insert into public.scratch_order values (2);"
    )
    (tmp_path / "0001_table.sql").write_text(
        "create table public.scratch_order (n int primary key);"
    )
    (tmp_path / "notes.txt").write_text("not sql")
    with psycopg.connect(pg_database, autocommit=True) as conn:
        try:
            ran = apply_sql_dir(conn, tmp_path)
            count = conn.execute("select count(*) from public.scratch_order").fetchone()
        finally:
            conn.execute("drop table if exists public.scratch_order")
    assert ran == ["0001_table.sql", "0002_rows.sql"]
    assert count[0] == 2


def test_a_missing_database_fails_the_run_and_does_not_skip_it():
    with pytest.raises(pytest.fail.Exception, match="make db-up"):
        open_scratch_database("postgresql://postgres:unused@127.0.0.1:1/postgres")


def test_the_seed_creates_the_development_user_one_time(pg_database, db):
    seed = (REPO_ROOT / "supabase" / "seed.sql").read_text(encoding="utf-8")
    with psycopg.connect(pg_database, autocommit=True) as conn:
        conn.execute(seed)
        conn.execute(seed)
        rows = conn.execute("select id from auth.users").fetchall()
    assert rows == [(DEV_USER_ID,)]



# --- The phase 1 seam: a state directory means a user -----------------------


def test_one_state_directory_is_one_user_each_time_it_is_opened(tmp_path):
    first_db, first_user = resolve(tmp_path)
    again_db, again_user = resolve(tmp_path)
    assert (again_db, again_user) == (first_db, first_user)
    # A different spelling of the same directory is the same store.
    assert resolve(tmp_path / "x" / "..")[1] == first_user


def test_two_state_directories_are_two_isolated_users(tmp_path, pg_database, scratch_table):
    database, honest = resolve(tmp_path / "honest")
    _, broken = resolve(tmp_path / "broken")
    assert honest != broken
    for user, value in ((honest, "a"), (broken, "b")):
        with database.tx(user) as conn:
            conn.execute(
                "insert into public.scratch_kv values (%s, 'same', %s)", (user, value)
            )
    assert sorted(_rows(pg_database)) == sorted(
        [(honest, "same", "a"), (broken, "same", "b")]
    )


def test_a_test_that_opens_no_store_does_not_touch_postgres(request):
    # The autouse fixture has run. It must not have asked for the database.
    assert "pg_database" not in request.fixturenames


def test_the_service_uses_the_development_user_and_needs_a_database_url(
    monkeypatch, pg_database
):
    monkeypatch.setattr(db_module, "_resolver", None)
    monkeypatch.setattr(db_module, "_default", None)
    monkeypatch.delenv(db_module.DATABASE_URL_ENV, raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        resolve("/any/state/dir")

    monkeypatch.setenv(db_module.DATABASE_URL_ENV, pg_database)
    database, user = resolve("/any/state/dir")
    try:
        assert user == DEV_USER_ID
        assert resolve("/another/state/dir") == (database, DEV_USER_ID)
    finally:
        database.close()
