"""The Postgres foundation: `Database.tx`, the test fixtures, and the seed."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from coworker.db import DEV_USER_ID, Database
from tests.pg_fixtures import REPO_ROOT, apply_sql_dir, connect_admin

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
        connect_admin("postgresql://postgres:unused@127.0.0.1:1/postgres")


def test_the_seed_creates_the_development_user_one_time(pg_database, db):
    seed = (REPO_ROOT / "supabase" / "seed.sql").read_text(encoding="utf-8")
    with psycopg.connect(pg_database, autocommit=True) as conn:
        conn.execute(seed)
        conn.execute(seed)
        rows = conn.execute("select id from auth.users").fetchall()
    assert rows == [(DEV_USER_ID,)]

