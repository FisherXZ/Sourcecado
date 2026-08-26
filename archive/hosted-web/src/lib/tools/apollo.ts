import { z } from "zod";
import type { Tool } from "./types";

const APOLLO_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search";
const APOLLO_MATCH_URL = "https://api.apollo.io/api/v1/people/match";

function requireApolloApiKey(): string {
  const apiKey = process.env.APOLLO_API_KEY;
  if (!apiKey) {
    throw new Error("APOLLO_API_KEY is not configured.");
  }
  return apiKey;
}

async function apolloPost(url: string, apiKey: string, body: Record<string, unknown>): Promise<unknown> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", "x-api-key": apiKey },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) {
    throw new Error(`Apollo request failed: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

// --- apollo_search_people ---

export const apolloSearchPeopleArgsSchema = z
  .object({
    organizationName: z.string().min(1).optional(),
    personTitles: z.array(z.string().min(1)).min(1).optional(),
    limit: z.number().int().positive().max(25).optional(),
  })
  .refine((v) => Boolean(v.organizationName) || Boolean(v.personTitles?.length), {
    message: "Provide organizationName or personTitles.",
  });
export type ApolloSearchPeopleArgs = z.infer<typeof apolloSearchPeopleArgsSchema>;

// Shape verified live against mixed_people/api_search on 2026-08-04 (50
// records). Search deliberately withholds contact detail on this plan: no
// `name`, `email`, or `linkedin_url` is ever sent — the last name arrives
// obfuscated ("Do***e") and email/phone reduce to existence signals. Reading
// fields Apollo does not send is what this shape replaces; do not add them back.
export interface ApolloPersonSummary {
  apolloId: string | null;
  firstName: string | null;
  lastNameObfuscated: string | null;
  title: string | null;
  organizationName: string | null;
  hasEmail: boolean;
  // Apollo sends a string here, not a boolean: "Yes" or "Maybe: please request
  // direct dial via people/bulk_match". Passed through verbatim — collapsing
  // "Maybe" into false would assert an absence Apollo never claimed.
  directPhoneStatus: string | null;
}

export interface ApolloSearchPeopleResult {
  people: ApolloPersonSummary[];
}

interface ApolloSearchResponse {
  people?: Array<{
    id?: string;
    first_name?: string;
    last_name_obfuscated?: string | null;
    title?: string;
    has_email?: boolean;
    has_direct_phone?: string;
    organization?: { name?: string };
  }>;
}

export const apolloSearchPeopleTool: Tool<ApolloSearchPeopleArgs, ApolloSearchPeopleResult> = {
  name: "apollo_search_people",
  description:
    "Search for people at a target organization via Apollo. Provide organizationName and/or personTitles. " +
    "Returns who exists and their titles only — no email and no full last name. To reach someone, resolve " +
    "their full name first (web_search / web_fetch), then call apollo_enrich_contact for a verified email.",
  permissionClass: "enrich",
  argsSchema: apolloSearchPeopleArgsSchema,
  async execute(args) {
    const apiKey = requireApolloApiKey();
    const data = (await apolloPost(APOLLO_SEARCH_URL, apiKey, {
      q_organization_name: args.organizationName,
      person_titles: args.personTitles,
      per_page: args.limit ?? 10,
    })) as ApolloSearchResponse;

    const people: ApolloPersonSummary[] = (data.people ?? []).map((p) => ({
      apolloId: p.id ?? null,
      firstName: p.first_name ?? null,
      lastNameObfuscated: p.last_name_obfuscated ?? null,
      title: p.title ?? null,
      organizationName: p.organization?.name ?? null,
      hasEmail: p.has_email ?? false,
      directPhoneStatus: p.has_direct_phone ?? null,
    }));
    return { people };
  },
};

// --- apollo_enrich_contact ---

export const apolloEnrichContactArgsSchema = z
  .object({
    email: z.string().min(1).optional(),
    firstName: z.string().min(1).optional(),
    lastName: z.string().min(1).optional(),
    organizationName: z.string().min(1).optional(),
  })
  .refine((v) => Boolean(v.email) || (Boolean(v.firstName) && Boolean(v.lastName)), {
    message: "Provide email, or firstName and lastName.",
  });
export type ApolloEnrichContactArgs = z.infer<typeof apolloEnrichContactArgsSchema>;

export interface ApolloEnrichContactResult {
  name: string | null;
  title: string | null;
  organizationName: string | null;
  linkedinUrl: string | null;
  email: string | null;
  phone: string | null;
}

interface ApolloMatchResponse {
  person?: {
    name?: string;
    title?: string;
    organization?: { name?: string };
    linkedin_url?: string;
    email?: string;
    phone_numbers?: Array<{ raw_number?: string }>;
  };
}

export const apolloEnrichContactTool: Tool<ApolloEnrichContactArgs, ApolloEnrichContactResult> = {
  name: "apollo_enrich_contact",
  description:
    "Enrich a single contact via Apollo. Provide email, or firstName + lastName (+ optional organizationName).",
  permissionClass: "enrich",
  argsSchema: apolloEnrichContactArgsSchema,
  async execute(args) {
    const apiKey = requireApolloApiKey();
    const data = (await apolloPost(APOLLO_MATCH_URL, apiKey, {
      email: args.email,
      first_name: args.firstName,
      last_name: args.lastName,
      organization_name: args.organizationName,
    })) as ApolloMatchResponse;

    const person = data.person;
    return {
      name: person?.name ?? null,
      title: person?.title ?? null,
      organizationName: person?.organization?.name ?? null,
      linkedinUrl: person?.linkedin_url ?? null,
      email: person?.email ?? null,
      phone: person?.phone_numbers?.[0]?.raw_number ?? null,
    };
  },
};
