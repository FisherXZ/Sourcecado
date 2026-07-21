import type { Sql } from "../tools/types";
import { findOrganizationsByExactName, type ContactSummary, type OrganizationSummary } from "./resolve";

// ---------------------------------------------------------------------------
// createContact — the write path for a new connection. Only `name` is
// required at the type/DB level: role and organization are gaps, not
// blockers, when the caller genuinely doesn't know them (rendered as such on
// the Contact Profile Card via the null columns themselves — no separate
// gap-tracking table needed). It is the *doctrine* layer (B1.7's system
// prompt update) that makes the agent ask the Director for role/org before
// calling this tool during a live chat; this function must still work when
// called with just a name (e.g. a future bulk-import path).
//
// organizationName resolution never guesses: an exact existing org is
// reused, a new name creates one, and an ambiguous exact match (two orgs
// already share that name) refuses to write and asks instead — the same
// "ask, don't guess" policy as resolveContact/resolveOrganization.
// ---------------------------------------------------------------------------

export interface CreateContactArgs {
  name: string;
  role?: string;
  organizationName?: string;
  phone?: string;
  email?: string;
  linkedinUrl?: string;
  photoUrl?: string;
}

export type CreateContactResult =
  | { status: "created"; contact: ContactSummary }
  | { status: "ambiguous_organization"; organizationName: string; candidates: OrganizationSummary[] };

export async function createContact(db: Sql, args: CreateContactArgs): Promise<CreateContactResult> {
  const name = args.name.trim();
  const role = args.role?.trim() || null;
  const organizationName = args.organizationName?.trim() || null;
  const phone = args.phone?.trim() || null;
  const email = args.email?.trim() || null;
  const linkedinUrl = args.linkedinUrl?.trim() || null;
  const photoUrl = args.photoUrl?.trim() || null;

  let organizationId: number | null = null;
  let resolvedOrganizationName: string | null = null;

  if (organizationName) {
    const existing = await findOrganizationsByExactName(db, organizationName);
    if (existing.length > 1) {
      return { status: "ambiguous_organization", organizationName, candidates: existing };
    }
    if (existing.length === 1) {
      organizationId = existing[0].id;
      resolvedOrganizationName = existing[0].name;
    } else {
      const [created] = await db<{ id: number | string }[]>`
        INSERT INTO organizations (name) VALUES (${organizationName}) RETURNING id
      `;
      organizationId = Number(created.id);
      resolvedOrganizationName = organizationName;
    }
  }

  const [row] = await db<{ id: number | string }[]>`
    INSERT INTO contacts (canonical_name, role, organization_id, phone, email, linkedin_url, photo_url)
    VALUES (${name}, ${role}, ${organizationId}, ${phone}, ${email}, ${linkedinUrl}, ${photoUrl})
    RETURNING id
  `;

  return {
    status: "created",
    contact: {
      id: Number(row.id),
      canonicalName: name,
      role,
      organizationId,
      organizationName: resolvedOrganizationName,
      phone,
      email,
      linkedinUrl,
      photoUrl,
    },
  };
}
