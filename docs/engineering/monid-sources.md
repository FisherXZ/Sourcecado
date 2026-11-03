# Monid sources

Sourcecado owns this connector. It installs no remote skill and exposes no new
MCP server. The operator connects one personal workspace in Connections using
a password field. `SecretStore` stores the key outside the repository with mode
0600. Keys and authorization headers never enter prompts, transcripts, sources
or public errors. No real Monid account was connected during implementation.

Monid's [application OAuth guide](https://docs.monid.ai/guide/quickstart-oauth.html)
requires an issued application client ID. The existing MCP OAuth helper is
Granola-specific. The documented single-workspace API-key option fits this
operator until a real OAuth client is issued. Saving a key reports **Key saved
— check connection**. Only an explicit successful `/v1/auth/whoami` check
marks it verified. A failed check clears the verified state.

## Current API and bounded capability

The [current OpenAPI contract](https://monid.ai/openapi.json), checked on
2026-11-02, takes precedence over older flat-input/numeric-price examples.

- Catalog: one `GET https://monid.ai/public/v1/endpoints?q=...&limit=...`, at
  most 20 cards, without following cursors or vendor instructions.
- Inspect: `POST https://api.monid.ai/v1/inspect`, exact provider/endpoint.
  Current input uses body/query/path locations; prices use nested USD amounts.
- Execute: `POST https://api.monid.ai/v1/run`, exact reviewed provider,
  endpoint and structured input. No wallet, key-minting, provisioning, account,
  billing or workspace configuration API is called.
- Refresh: `GET /v1/runs/{runId}` for a saved person-owned approved run. It
  never starts or retries research.

Catalog entries can be read, but only audited Exa `search`/`/search` is
executable initially. It accepts a query up to 1000 characters and 1–10 results
in `input.body`. Other endpoints remain visibly catalog-only. Execution also
requires a simple fixed USD `PER_CALL` price without tiers, selectors, variants,
metering or accrual. An incompatible live input/price contract is refused;
this does not promise that every currently listed Exa price is supported.

## Quote, approval and evidence

The internal sourcing agent has `monid_catalog`, `monid_inspect`,
`monid_prepare_research`, `monid_research` and `monid_read_result`.
Preparation saves an immutable two-minute local quote for the chat's bound
person: endpoint, complete input, fixed quote, chosen maximum and credential
fingerprint. It starts no research. HTTP and chat review the same quote.
`monid_research` requires a specific once-only claimed Inbox decision. Always,
session scope and a bare approval boolean cannot authorize it.

Execution checks the stored claim, claimant, arguments/resource, person/chat,
credential and expiry, then re-inspects the endpoint and price before dispatch.
A durable person/run reservation and the existing external-effect fence precede
the paid call. A quote cannot dispatch again through another approval. Unknown
outcomes stop new research. Restart or replacing a key cannot erase an
unresolved paid effect in the run ledger; the operator reviews it through the
existing recovery queue. No automatic paid retry occurs.

**Cost limit:** Sourcecado compares the maximum with the fixed quote before
dispatch. The current vendor `RunRequest` has no atomic per-run charge ceiling,
so this is not a guarantee about the final vendor charge. Actual cost comes
only from explicit `billing.reportedCost` and its USD unit. Absent billing stays
**Unknown**, never the quote or an invented zero. Reported overruns are saved
truthfully and stop further spending until review and an explicit reconnect.
Sourcecado does not change provider budget settings.

Results are bounded, redacted, untrusted person-owned source references and
artifacts with provider, provider run ID, Sourcecado run ID, approval ID,
endpoint, quote/maximum and reported cost. They never update identity,
sequences, tasks or outreach. Provider hints are not executed; agent results
remain in external evidence envelopes.

`test_monid.py` uses fake transport for connection/redaction, bounded catalog,
current input/price shape, caps, denial/standing scope refusal, drift, HTTP/chat
claims and fences, unknown/restart/reconnect safety, result reads, cost absence
and overruns, source binding and unchanged identity. See
[component evidence](../evidence/2026-11-02-monid/README.md).

Real key validity, workspace funds, live pricing/data quality and a paid lookup
remain unverified. Credentials and a separate specific lookup approval are
required. Generic release/merge approval does not authorize connecting an
account or spending.
