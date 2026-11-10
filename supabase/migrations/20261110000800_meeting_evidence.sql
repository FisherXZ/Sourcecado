-- Migration slice S08: MeetingEvidenceStore moves from meeting_evidence.db to Postgres.
-- Head schema of the SQLite store, plus user_id on every table and key.
-- evidence_id is a hash of (provider, provider_id), so two users who see the
-- same meeting get the same evidence_id. The keys include user_id for that.

create table meeting_evidence (
    user_id uuid not null references auth.users(id) on delete cascade,
    evidence_id text not null,
    provider text not null,
    provider_id text not null,
    title text not null,
    starts_at text,
    ends_at text,
    participants_json text not null,
    source_ref_json text not null,
    notes text,
    status text not null,
    person_id text,
    match_reason text,
    created_at text not null,
    updated_at text not null,
    primary key (user_id, evidence_id),
    unique (user_id, provider, provider_id)
);

create index meeting_evidence_user_person_idx
    on meeting_evidence (user_id, person_id);

create table meeting_candidates (
    user_id uuid not null references auth.users(id) on delete cascade,
    evidence_id text not null,
    person_id text not null,
    status text not null,
    match_reason text not null,
    updated_at text not null,
    primary key (user_id, evidence_id, person_id)
);

create index meeting_candidates_user_person_idx
    on meeting_candidates (user_id, person_id);
