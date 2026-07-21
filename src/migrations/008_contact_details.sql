-- 008_contact_details.sql — B1.9: contact detail fields for the Profile Card.
-- All four are optional, same gap-not-blocker treatment as role/organization_id
-- in 006_contacts.sql. photo_url is a plain URL for now — auto-populating it
-- from a confirmed LinkedIn match is C3's job (guided data-collection workflow),
-- not this migration's.

ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS phone        TEXT,
  ADD COLUMN IF NOT EXISTS email        TEXT,
  ADD COLUMN IF NOT EXISTS linkedin_url TEXT,
  ADD COLUMN IF NOT EXISTS photo_url    TEXT;
