-- Local development only. `supabase db reset` runs this file.
-- The one user that the local stack runs as until sign-in exists (migration
-- slice S13). The id matches DEV_USER_ID in backend/coworker/db.py.
insert into auth.users (id, email)
values ('00000000-0000-4000-8000-000000000001', 'dev@sourcecado.test')
on conflict (id) do nothing;
