-- Test databases only. Supabase creates the real auth.users table.
-- The migrations have foreign keys to it, so a test database needs a table
-- with the same key.
create schema if not exists auth;

create table if not exists auth.users (
  id uuid primary key,
  email text
);
