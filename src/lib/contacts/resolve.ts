import type { Sql } from "../tools/types";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface ContactSummary {
  id: number;
  canonicalName: string;
  role: string | null;
  organizationId: number | null;
  organizationName: string | null;
}

export type ContactResolution =
  | { status: "found"; contact: ContactSummary }
  | { status: "ambiguous"; candidates: ContactSummary[] }
  | { status: "not_found" };

export interface OrganizationSummary {
  id: number;
  name: string;
  domain: string | null;
}

export interface OrganizationContact {
  id: number;
  canonicalName: string;
  role: string | null;
}

export type OrganizationResolution =
  | { status: "found"; organization: OrganizationSummary; contacts: OrganizationContact[] }
  | { status: "ambiguous"; candidates: OrganizationSummary[] }
  | { status: "not_found" };

// ---------------------------------------------------------------------------
// resolveContact — exact canonical-name or alias match only. No fuzzy/trigram
// matching: a near-miss with zero exact match is "not_found", not a guess.
// More than one exact match (shared name, or the same alias on two different
// people) is "ambiguous" — the caller (harness doctrine) asks which one is
// meant rather than the tool silently picking one.
// ---------------------------------------------------------------------------

interface ContactRow {
  id: number | string;
  canonical_name: string;
  role: string | null;
  organization_id: number | string | null;
  organization_name: string | null;
}

function mapContact(row: ContactRow): ContactSummary {
  return {
    id: Number(row.id),
    canonicalName: row.canonical_name,
    role: row.role,
    organizationId: row.organization_id === null ? null : Number(row.organization_id),
    organizationName: row.organization_name,
  };
}

export async function resolveContact(db: Sql, name: string): Promise<ContactResolution> {
  const trimmed = name.trim();
  const rows = await db<ContactRow[]>`
    SELECT c.id, c.canonical_name, c.role,
           o.id AS organization_id, o.name AS organization_name
    FROM contacts c
    LEFT JOIN organizations o ON o.id = c.organization_id
    WHERE lower(c.canonical_name) = lower(${trimmed})
       OR EXISTS (
         SELECT 1 FROM contact_aliases a
         WHERE a.contact_id = c.id AND lower(a.alias) = lower(${trimmed})
       )
    ORDER BY c.id
  `;

  if (rows.length === 0) return { status: "not_found" };
  if (rows.length === 1) return { status: "found", contact: mapContact(rows[0]) };
  return { status: "ambiguous", candidates: rows.map(mapContact) };
}

// ---------------------------------------------------------------------------
// resolveOrganization — same exact-match-only policy. On a single match,
// also returns the known contacts at that org (the "who do we know at
// Company X" lookup).
// ---------------------------------------------------------------------------

interface OrgRow {
  id: number | string;
  name: string;
  domain: string | null;
}

function mapOrg(row: OrgRow): OrganizationSummary {
  return { id: Number(row.id), name: row.name, domain: row.domain };
}

// Shared by resolveOrganization and createContact — both need "does an org with
// exactly this name already exist" without duplicating the query/mapping.
export async function findOrganizationsByExactName(db: Sql, name: string): Promise<OrganizationSummary[]> {
  const trimmed = name.trim();
  const rows = await db<OrgRow[]>`
    SELECT id, name, domain FROM organizations
    WHERE lower(name) = lower(${trimmed})
    ORDER BY id
  `;
  return rows.map(mapOrg);
}

export async function resolveOrganization(db: Sql, name: string): Promise<OrganizationResolution> {
  const orgs = await findOrganizationsByExactName(db, name);

  if (orgs.length === 0) return { status: "not_found" };
  if (orgs.length > 1) return { status: "ambiguous", candidates: orgs };

  const organization = orgs[0];
  const contactRows = await db<{ id: number | string; canonical_name: string; role: string | null }[]>`
    SELECT id, canonical_name, role FROM contacts
    WHERE organization_id = ${organization.id}
    ORDER BY canonical_name
  `;
  const contacts: OrganizationContact[] = contactRows.map((r) => ({
    id: Number(r.id),
    canonicalName: r.canonical_name,
    role: r.role,
  }));

  return { status: "found", organization, contacts };
}
