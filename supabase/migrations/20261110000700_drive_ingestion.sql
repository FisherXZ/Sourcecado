-- Migration slice S07: DriveIngestionStore moves from drive_ingestion.db to Postgres.
-- Head schema of the SQLite store, plus user_id on every table and key.
-- Every text column is collate "C": bytewise, as SQLite compares text.

create table drive_ingestion_jobs (
    user_id uuid not null references auth.users(id) on delete cascade,
    id text collate "C" not null,
    folder_id text collate "C" not null,
    resolved_path text collate "C" not null,
    status text collate "C" not null,
    generation bigint not null,
    work_revision bigint not null default 1,
    cancel_requested bigint not null default 0,
    created_at text collate "C" not null,
    updated_at text collate "C" not null,
    primary key (user_id, id)
);

create index drive_ingestion_jobs_user_created_idx
    on drive_ingestion_jobs (user_id, created_at desc, id desc);

create table drive_ingestion_folders (
    user_id uuid not null references auth.users(id) on delete cascade,
    job_id text collate "C" not null,
    drive_id text collate "C" not null,
    parent_id text collate "C",
    path text collate "C" not null,
    status text collate "C" not null,
    page_token text collate "C",
    generation bigint not null,
    error_kind text collate "C",
    primary key (user_id, job_id, drive_id)
);

create table drive_ingestion_sources (
    user_id uuid not null references auth.users(id) on delete cascade,
    job_id text collate "C" not null,
    drive_id text collate "C" not null,
    scope text collate "C" not null default 'tree',
    parent_id text collate "C",
    path text collate "C" not null,
    name text collate "C" not null,
    mime_type text collate "C" not null,
    modified_time text collate "C",
    web_view_link text collate "C",
    sensitivity text collate "C" not null default 'standard',
    extraction_status text collate "C" not null default 'pending',
    content text collate "C",
    citations_json text collate "C" not null default '[]',
    redaction_count bigint not null default 0,
    source_safety_json text collate "C",
    generation bigint not null,
    last_action text collate "C" not null default 'pending',
    error_kind text collate "C",
    deleted bigint not null default 0,
    needs_read bigint not null default 1,
    primary key (user_id, job_id, drive_id)
);

create table drive_ingestion_proposals (
    user_id uuid not null references auth.users(id) on delete cascade,
    id text collate "C" not null,
    job_id text collate "C" not null,
    source_drive_id text collate "C" not null,
    person_id text collate "C" not null,
    record_type text collate "C" not null,
    fields_json text collate "C" not null,
    diff_json text collate "C" not null,
    source_refs_json text collate "C" not null,
    status text collate "C" not null,
    created_at text collate "C" not null,
    reviewed_at text collate "C",
    reviewer text collate "C",
    primary key (user_id, id)
);

create index drive_ingestion_proposals_user_job_idx
    on drive_ingestion_proposals (user_id, job_id, created_at, id);

-- Row-level security on, with no policy: the public API keys see no rows.
-- The backend connects as the table owner, so it is not affected.
alter table drive_ingestion_jobs enable row level security;
alter table drive_ingestion_folders enable row level security;
alter table drive_ingestion_sources enable row level security;
alter table drive_ingestion_proposals enable row level security;
