-- Migration slice S07: DriveIngestionStore moves from drive_ingestion.db to Postgres.
-- Head schema of the SQLite store, plus user_id on every table and key.

create table drive_ingestion_jobs (
    user_id uuid not null references auth.users(id) on delete cascade,
    id text not null,
    folder_id text not null,
    resolved_path text not null,
    status text not null,
    generation bigint not null,
    work_revision bigint not null default 1,
    cancel_requested bigint not null default 0,
    created_at text not null,
    updated_at text not null,
    primary key (user_id, id)
);

create index drive_ingestion_jobs_user_created_idx
    on drive_ingestion_jobs (user_id, created_at desc, id desc);

create table drive_ingestion_folders (
    user_id uuid not null references auth.users(id) on delete cascade,
    job_id text not null,
    drive_id text not null,
    parent_id text,
    path text not null,
    status text not null,
    page_token text,
    generation bigint not null,
    error_kind text,
    primary key (user_id, job_id, drive_id)
);

create table drive_ingestion_sources (
    user_id uuid not null references auth.users(id) on delete cascade,
    job_id text not null,
    drive_id text not null,
    scope text not null default 'tree',
    parent_id text,
    path text not null,
    name text not null,
    mime_type text not null,
    modified_time text,
    web_view_link text,
    sensitivity text not null default 'standard',
    extraction_status text not null default 'pending',
    content text,
    citations_json text not null default '[]',
    redaction_count bigint not null default 0,
    source_safety_json text,
    generation bigint not null,
    last_action text not null default 'pending',
    error_kind text,
    deleted bigint not null default 0,
    needs_read bigint not null default 1,
    primary key (user_id, job_id, drive_id)
);

create table drive_ingestion_proposals (
    user_id uuid not null references auth.users(id) on delete cascade,
    id text not null,
    job_id text not null,
    source_drive_id text not null,
    person_id text not null,
    record_type text not null,
    fields_json text not null,
    diff_json text not null,
    source_refs_json text not null,
    status text not null,
    created_at text not null,
    reviewed_at text,
    reviewer text,
    primary key (user_id, id)
);

create index drive_ingestion_proposals_user_job_idx
    on drive_ingestion_proposals (user_id, job_id, created_at, id);
