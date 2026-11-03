# Connector health and credential renewal

`GET /v1/connectors` reads saved credentials and the last applicable probe result.
It does not call external services. Connections and Settings then request
`POST /v1/connectors/check` to check Google and Granola. Apollo remains
Configured and unverified: the check never spends enrichment credits.

Google checks exchange the saved refresh token. Granola checks call
`list_meetings`, renewing and retrying once after an HTTP 401. An MCP tool's
`isError` flag is a failure even when the transport succeeded. Sourcecado returns
fixed error wording and discards the provider's error content, which can contain
credentials.

Results are cached for 60 seconds against a one-way fingerprint of the credential
actually checked. A Granola attempt pins the token profile it uses; a retry after
renewal carries the renewed profile's fingerprint. Google renewal likewise
identifies the exchanged refresh token, including rotation. A reconnect that
finishes during a check cannot inherit the old grant's result. A late old result
also cannot replace a newer grant's cached result.

Renewal saves tokens only if the saved profiles have not changed while the token
endpoint was responding. A concurrent reconnect or disconnect wins: an old
renewal cannot restore removed credentials or overwrite the new sign-in. Google
updates its shared Google/Gmail profiles together under the secret store's lock.

Rejected grants use Reconnect required. Unreachable services and tool failures
use Connection failed. Both screens use the same catalog, status vocabulary and
repair routes. These checks never create drafts, send messages or log tokens.

Verification starts with `test_connector_probes.py`, `test_connector_probe_safety.py`
and `test_token_renewal.py`. The safety cases exercise official MCP error objects,
reconnect/disconnect during outstanding calls, rotated tokens and late results
without calling real connectors.
