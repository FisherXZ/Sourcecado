import { z } from "zod";
import type { Tool } from "./types";

const APOLLO_SEARCH_URL = "https://api.apollo.io/v1/mixed_people/search";
const APOLLO_MATCH_URL = "https://api.apollo.io/v1/people/match";

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

export interface ApolloPersonSummary {
  name: string | null;
  title: string | null;
  organizationName: string | null;
  linkedinUrl: string | null;
  email: string | null;
}

export interface ApolloSearchPeopleResult {
  people: ApolloPersonSummary[];
}

interface ApolloSearchResponse {
  people?: Array<{
    name?: string;
    title?: string;
    organization?: { name?: string };
    linkedin_url?: string;
    email?: string;
  }>;
}

export const apolloSearchPeopleTool: Tool<ApolloSearchPeopleArgs, ApolloSearchPeopleResult> = {
  name: "apollo_search_people",
  description:
    "Search for people at a target organization via Apollo. Provide organizationName and/or personTitles.",
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
      name: p.name ?? null,
      title: p.title ?? null,
      organizationName: p.organization?.name ?? null,
      linkedinUrl: p.linkedin_url ?? null,
      email: p.email ?? null,
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
