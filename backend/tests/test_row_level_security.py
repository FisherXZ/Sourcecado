"""Every table in the public schema has row-level security on.

On hosted Supabase, a public table with row-level security off can be read
and written through the public API key. A table with it on and no policy
gives that key no rows. The backend connects as the table owner, so the
stores are not affected.
"""

import psycopg


def test_every_public_table_has_row_level_security_enabled(pg_database):
    with psycopg.connect(pg_database) as conn:
        rows = conn.execute(
            """
            select c.relname, c.relrowsecurity
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relkind in ('r', 'p')
            order by c.relname
            """
        ).fetchall()

    assert rows, "the test database has no public tables"
    assert [name for name, enabled in rows if not enabled] == []
