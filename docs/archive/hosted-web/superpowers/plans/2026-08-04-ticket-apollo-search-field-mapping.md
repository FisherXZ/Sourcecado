# Ticket — `apollo_search_people` reads fields Apollo does not return

Date: 2026-08-04
Found by: live probe against the real Apollo API (first run since a key existed)
Blocks: nothing, but every Apollo search result is currently near-empty
Est: 0.25

## Problem

`src/lib/tools/apollo.ts` maps the search response like this:

```ts
name: p.name ?? null,
title: p.title ?? null,
organizationName: p.organization?.name ?? null,
linkedinUrl: p.linkedin_url ?? null,
email: p.email ?? null,
```

Three of those five fields do not exist on `mixed_people/api_search` responses.
The tool returns objects where only `title` and `organizationName` are ever
populated; `name`, `linkedinUrl`, and `email` are always `null`.

## Evidence

Raw probe, 2026-08-04, `POST /api/v1/mixed_people/api_search` with
`{ q_organization_name: "Anthropic", per_page: 2 }` → HTTP 200. Actual keys on
`people[0]`:

```
id, first_name, last_name_obfuscated, title, last_refreshed_at,
has_email, has_city, has_state, has_country, has_direct_phone, organization
```

Sample values: `first_name: "Komei"`, `title: "GTM Recruiting"`, no `name`, no
`email`, no `linkedin_url`. Top-level response keys are `total_entries` and
`people` only — there is no `pagination` object either.

## What is NOT the fix

Do not try to make search return emails or full names. On this Apollo plan the
search endpoint deliberately obfuscates the last name and exposes only
`has_email` / `has_direct_phone` booleans as existence signals. That is a
commercial tier limit, not a code defect. `people/match` (our
`apollo_enrich_contact`) is the endpoint that returns a real, verified email —
verified live in the same probe.

## Fix

Reshape `ApolloPersonSummary` and its mapping to the fields Apollo actually
sends. Suggested shape, but confirm against a freshly captured response rather
than against this doc:

- `firstName` ← `first_name`
- `lastNameObfuscated` ← `last_name_obfuscated`
- `title`, `organizationName` — unchanged, both real
- `hasEmail` ← `has_email`, `hasDirectPhone` ← `has_direct_phone`
  - **Correction from the re-probe (50 records, 2026-08-04):** `has_email` is a
    real boolean (true and false both observed), but `has_direct_phone` is a
    *string*, never a boolean — observed values are `"Yes"` (37/50) and
    `"Maybe: please request direct dial via people/bulk_match"` (13/50). No
    negative value was observed at all. Shipped as `directPhoneStatus: string
    | null`, passed through verbatim: a boolean would have to collapse "Maybe"
    into `false`, asserting an absence Apollo never claimed — the same class of
    error as the original bug. `last_name_obfuscated` is also nullable (1/50).
- `apolloId` ← `id` (needed to feed a later enrich call)
- Drop `email` and `linkedinUrl` from the search result type entirely — carrying
  a field that is structurally always `null` invites exactly this class of bug.

`apollo_enrich_contact`'s mapping is correct and out of scope; every field it
reads exists on the match response.

## Why the tests did not catch this

`tests/apollo-tools.test.ts` mocks the response using the *assumed* shape, so
the mapping and the fixture agree with each other and disagree with Apollo. The
suite was green on a wrong mapping for two weeks.

Rewrite the fixtures from a real captured response (redact the key, keep the
field names). This is the actual lesson of the ticket: a mock written from the
same assumption as the code under test proves nothing.

## Consequence worth recording

Because search cannot yield a full name, search results cannot be piped straight
into enrich. The working sequence is:

**Apollo search** (who exists, what titles) → **web_search / web_fetch** (resolve
the full name) → **Apollo enrich** (name → verified email) → Gmail draft.

The three-tool loop is structurally required, not a stylistic choice. Any
routine or agent prompt that assumes search alone produces contactable people is
wrong.

## Done when

- `apollo_search_people` returns only fields Apollo actually sends, with no
  always-`null` members in the result type.
- `tests/apollo-tools.test.ts` fixtures are derived from a real captured
  response, and would fail against the old mapping.
- Full suite green; `tsc --noEmit` and `npm run build` clean.
