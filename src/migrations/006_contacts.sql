-- 006_contacts.sql — B1: Organizations & Contacts + identity resolution.
-- canonical_name is required; role and organization_id are not — a contact created from
-- a thin mention is a known gap (rendered as such on the Contact Profile Card), not a
-- blocked write. canonical_name and alias are deliberately NOT unique: two different real
-- people can share a name, and the same alias can point at two different contacts — that
-- ambiguity is resolved by get_contact (B1.2), never hidden by a schema constraint.

CREATE TABLE IF NOT EXISTS organizations (
  id             BIGSERIAL PRIMARY KEY,
  name           TEXT NOT NULL,
  domain         TEXT,
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contacts (
  id              BIGSERIAL PRIMARY KEY,
  canonical_name  TEXT NOT NULL,
  role            TEXT,
  organization_id BIGINT REFERENCES organizations(id) ON DELETE SET NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contact_aliases (
  id          BIGSERIAL PRIMARY KEY,
  contact_id  BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  alias       TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (contact_id, alias)
);

CREATE INDEX IF NOT EXISTS contacts_organization_idx ON contacts(organization_id);
CREATE INDEX IF NOT EXISTS contacts_canonical_name_idx ON contacts(lower(canonical_name));
CREATE INDEX IF NOT EXISTS contact_aliases_alias_idx ON contact_aliases(lower(alias));
