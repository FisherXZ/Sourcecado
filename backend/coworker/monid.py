"""Sourcecado-owned Monid research. Catalog text and results are untrusted.

Only audited, bounded Exa search with a simple fixed USD per-call price can
execute. Monid has no atomic request charge ceiling: our quote/cap check is a
pre-dispatch guard, not a promise about the vendor's eventual charge.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from coworker.apollo import HttpError, LiveHttp

API = "https://api.monid.ai"
PUBLIC = "https://monid.ai/public/v1/endpoints"
MAX_USD = Decimal("10")


class MonidError(ValueError):
    pass


class MonidOutcomeUnknown(RuntimeError):
    pass


def money(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise MonidError("A finite USD amount is required.") from None
    if not number.is_finite() or number < 0 or number > MAX_USD:
        raise MonidError("The USD amount must be between 0 and 10.")
    return number


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def saved_research(person):
    """Saved research on one expanded person record, newest first. Never calls Monid."""
    rows = [row for row in person.get("attachments") or []
            if row.get("type") == "artifact" and (row.get("fields") or {}).get("artifact_type") == "monid_research"]
    rows.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
    latest = {}
    for row in rows:
        fields = row["fields"]
        latest.setdefault(fields.get("approval_id"), {
            **{key: fields.get(key) for key in ("approval_id", "status", "remote_provider", "endpoint", "quoted_cost_usd",
                "max_cost_usd", "actual_cost_usd", "cost_unknown", "over_budget", "output")},
            "saved_at": row.get("updated_at") or row.get("created_at")})
    return list(latest.values())[:20]


class MonidResearchActions:
    def __init__(self, *, people, store, inbox, secrets_store, http=None, agent_runs=None):
        self.people, self.store, self.inbox, self.secrets = people, store, inbox, secrets_store
        self.http = http if http is not None else LiveHttp()
        self.agent_runs = agent_runs

    def _unsettled(self):
        repository = self.agent_runs() if callable(self.agent_runs) else self.agent_runs
        if repository is None:
            return False
        return any(effect["tool_name"] == "monid_research" for effect in repository.list_quarantined_effects(limit=10000))

    def status(self):
        profile = self.secrets.get("monid") or {}
        return {"configured": bool(profile.get("api_key")), "verified": bool(profile.get("verified")),
                "workspace": profile.get("workspace_name"), "checked_at": profile.get("checked_at"),
                "spend_paused": bool(profile.get("spend_paused")) or self._unsettled()}

    def save_key(self, value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{12,512}", value):
            raise MonidError("Enter a personal Monid API key without spaces.")
        self.secrets.put("monid", {"api_key": value, "verified": False})
        return self.status()

    def _profile(self, verified=False):
        profile = self.secrets.get("monid") or {}
        if not profile.get("api_key") or (verified and not profile.get("verified")):
            raise MonidError("Monid is not connected. Save and check a personal workspace API key in Connections.")
        if verified and (profile.get("spend_paused") or self._unsettled()):
            raise MonidError("Monid spending is stopped after an uncertain outcome or reported charge over the maximum. Review the provider record before reconnecting.")
        return profile

    def _safe(self, value):
        key = str((self.secrets.get("monid") or {}).get("api_key") or "")
        text = json.dumps(value, ensure_ascii=False, default=str)
        if key:
            text = text.replace(key, "[redacted]")
        # Never copy arbitrary authorization or credential fields into a source.
        def clean(item, depth=0):
            if depth > 8:
                return "[truncated]"
            if isinstance(item, dict):
                return {str(k)[:100]: clean(v, depth+1) for k, v in list(item.items())[:40]
                        if not re.search(r"authorization|api.?key|password|secret|token", str(k), re.I)}
            if isinstance(item, list):
                return [clean(v, depth+1) for v in item[:20]]
            return re.sub(r"(?i)\bBearer\s+\S+", "[authorization redacted]", item[:8000]) if isinstance(item, str) else item
        if len(text) > 128000:
            return {"truncated": True, "message": "The provider output exceeded the local evidence limit."}
        return clean(json.loads(text))

    def _request(self, method, path, *, payload=None, paid=False, expected_credential=None):
        profile = self._profile()
        if expected_credential and fingerprint(profile["api_key"]) != expected_credential:
            raise MonidError("The Monid key changed before dispatch. Review a new quote.")
        headers = {"Authorization": f"Bearer {profile['api_key']}", "Content-Type": "application/json"}
        try:
            if method == "GET":
                return self.http.get(API + path, headers=headers)
            return self.http.post(API + path, headers=headers, json=payload)
        except HttpError as exc:
            if paid and isinstance(exc.body, dict) and exc.body.get("runId"):
                return exc.body
            if exc.status == 402:
                raise MonidError("Monid reports insufficient funds. No automatic retry will run.") from None
            raise MonidError("Monid refused the request. Check the connection and request, then review a new attempt.") from None
        except Exception:
            if paid:
                # Propagates to guarded_call, which quarantines the dispatched effect.
                raise MonidOutcomeUnknown("Monid did not confirm the outcome. Do not retry this lookup automatically.") from None
            raise MonidError("Monid could not be reached. No new research run was started.") from None

    def check(self):
        profile = self._profile()
        try:
            data = self._request("GET", "/v1/auth/whoami")
        except MonidError:
            self.secrets.replace_if_unchanged({"monid": profile}, {"monid": {**profile, "verified": False}})
            raise
        workspace = data.get("workspace") if isinstance(data, dict) else None
        if not isinstance(workspace, dict) or not workspace.get("workspaceId"):
            raise MonidError("This key has no personal workspace. Choose a workspace-bound Monid API key.")
        replacement = {**profile, "verified": True, "workspace_name": self._safe(str(workspace.get("name") or "Personal workspace"))[:200],
                       "workspace_id": str(workspace["workspaceId"]), "checked_at": time.time()}
        if not self.secrets.replace_if_unchanged({"monid": profile}, {"monid": replacement}):
            raise MonidError("The key changed during the check. Check the current key again.")
        return self.status()

    def catalog(self, query, limit=5):
        if not isinstance(query, str) or len(query) > 200:
            raise MonidError("Catalog search must be at most 200 characters.")
        limit = min(max(int(limit), 1), 20)
        try:
            data = self.http.get(PUBLIC, params={"q": query, "limit": limit})
        except Exception:
            raise MonidError("Couldn’t read the Monid catalog. No paid call was made.") from None
        rows = []
        for item in (data.get("items", []) if isinstance(data, dict) else [])[:limit]:
            if not isinstance(item, dict):
                continue
            row = {k: item.get(k) for k in ("provider", "endpoint", "displayName", "displayDescription", "price")}
            row["supported"] = item.get("provider") == "exa" and item.get("endpoint") in {"search", "/search"}
            rows.append(self._safe(row))
        return {"items": rows, "untrusted": True}

    def inspect(self, provider, endpoint):
        self._profile(verified=True)
        if not isinstance(provider, str) or not isinstance(endpoint, str) or len(provider) > 100 or len(endpoint) > 300:
            raise MonidError("Choose a catalog provider and endpoint.")
        data = self._request("POST", "/v1/inspect", payload={"provider": provider, "endpoint": endpoint})
        if not isinstance(data, dict) or data.get("provider") != provider or data.get("endpoint") != endpoint:
            raise MonidError("The inspected endpoint does not match the selected endpoint.")
        return self._safe({k: data.get(k) for k in ("provider", "endpoint", "description", "method", "price", "input")})

    def _price(self, detail):
        price = detail.get("price") or {}
        if not isinstance(price, dict):
            raise MonidError("The provider did not supply a usable fixed price.")
        amount = price.get("amount") or {}
        if not isinstance(amount, dict):
            raise MonidError("A structured USD price is required; legacy price formats cannot authorize spending.")
        if (price.get("type") != "PER_CALL" or amount.get("currency") != "USD"
                or any(price.get(k) for k in ("variants", "tiers", "selectors", "period", "per", "unit", "default", "flatFee"))):
            raise MonidError("Only a simple fixed USD per-call quote is supported. Metered or variable research is unavailable.")
        return str(money(amount.get("value")))

    def _bound(self, pid, sid):
        if self.people.get(pid) is None or not sid or self.people.person_for_session(sid) != pid:
            raise MonidError("Open the selected person’s bound sourcing chat before preparing research.")

    def prepare(self, pid, sid, provider, endpoint, input, max_cost_usd, *, run_id=None):
        self._bound(pid, sid)
        profile = self._profile(verified=True)
        if provider != "exa" or endpoint not in {"search", "/search"}:
            raise MonidError("This endpoint is catalog-only in Sourcecado. Only audited Exa web search can run.")
        if not isinstance(input, dict) or self._safe(input) != input:
            raise MonidError("Research input must be a structured object without credentials.")
        body = input.get("body")
        if (set(input or {}) != {"body"} or not isinstance(body, dict) or set(body) != {"query", "numResults"}
                or not isinstance(body["query"], str) or not 1 <= len(body["query"].strip()) <= 1000
                or type(body["numResults"]) is not int or not 1 <= body["numResults"] <= 10):
            raise MonidError("Use structured body input with a query and 1–10 results. Other input is not executable.")
        cap = str(money(max_cost_usd))
        detail = self.inspect(provider, endpoint)
        locations = detail.get("input")
        schema = locations.get("body") if isinstance(locations,dict) else None
        if not isinstance(schema,dict) or not isinstance(schema.get("properties"),dict):
            raise MonidError("The provider did not supply a usable structured body schema.")
        if (detail.get("method") != "POST" or not {"query", "numResults"} <= set(schema.get("properties", {}))
                or set(schema.get("required", [])) - set(body)):
            raise MonidError("The provider input contract changed. This lookup cannot be prepared safely.")
        price = self._price(detail)
        if money(price) > money(cap):
            raise MonidError("The quote exceeds the chosen maximum. No run was started.")
        quote = {"kind": "monid_research", "quote_id": "monid_" + secrets.token_hex(16),
                 "person_id": pid, "session_id": sid, "provider": provider, "endpoint": endpoint,
                 "input": input, "quoted_cost_usd": price, "max_cost_usd": cap, "currency": "USD",
                 "expires_at": time.time()+120, "credential_fingerprint": fingerprint(profile["api_key"]),
                 "contract_fingerprint": fingerprint(json.dumps(detail, sort_keys=True)), "origin_run_id": run_id}
        person = self.people.get(pid)
        quote["identity_revision"] = person["identity_revision"]
        quote["person_label"] = " ".join(str(person.get(k) or "") for k in ("first_name","last_name")).strip() or "Saved person"
        title = f"Monid quote: {provider} {endpoint}, USD {price} (maximum USD {cap})"
        self.people.upsert_attachment(pid, record_type="artifact", fields={"artifact_type": "monid_quote", "title": title, "quote": quote},
            idempotency_key=quote["quote_id"], actor="assistant", rationale_summary="Prepared a bounded Monid research quote; no paid call.",
            session_id=sid, run_id=run_id)
        return quote

    def approval_resource(self, args, session_id, *, check_expiry=True):
        qid = args.get("quote_id") if isinstance(args, dict) else None
        with self.people._lock:
            row = self.people._conn.execute("SELECT fields_json FROM person_attachments WHERE record_type='artifact' AND idempotency_key=?", (qid,)).fetchone()
        fields = json.loads(row[0]) if row else {}
        quote = fields.get("quote")
        if fields.get("artifact_type") != "monid_quote" or not isinstance(quote, dict):
            raise MonidError("Prepare a specific Monid lookup quote before asking for approval.")
        self._bound(quote["person_id"], session_id)
        if self.people.get(quote["person_id"])["identity_revision"] != quote.get("identity_revision"):
            raise MonidError("The selected person’s identity changed. Review a new research quote.")
        # The window bounds how long a quote may wait before review; after a human
        # approves, dispatch rechecks the live price and contract instead.
        if quote["session_id"] != session_id or (check_expiry and quote["expires_at"] < time.time()):
            raise MonidError("This Monid quote expired or belongs to a different chat. Review a fresh quote.")
        if quote["credential_fingerprint"] != fingerprint(self._profile(verified=True)["api_key"]):
            raise MonidError("The Monid connection changed. Review a fresh quote.")
        return quote

    def park(self, qid, sid):
        quote = self.approval_resource({"quote_id": qid}, sid)
        return self.inbox.park("monid_research", {"quote_id": qid}, resource=quote,
            reason=f"One {quote['provider']} {quote['endpoint']} lookup quoted at USD {quote['quoted_cost_usd']}; maximum USD {quote['max_cost_usd']}.",
            session_id=sid, run_id=quote.get("origin_run_id"))

    def execute(self, args, *, approval_id, approval_claimant, run_id, session_id):
        item = self.inbox.get(str(approval_id or "")) or {}
        if not (run_id and approval_claimant and item.get("name") == "monid_research"
                and item.get("execution_status") == "executing" and item.get("execution_claimant") == approval_claimant
                and item.get("decision") == "allow" and item.get("scope") == "once"
                and item.get("arguments") == args and item.get("session_id") == session_id):
            return False, {"error": "A specific once-approved, durably claimed Monid lookup is required.", "code": "approval_required"}
        dispatched = False
        try:
            quote = self.approval_resource(args, session_id, check_expiry=False)
            resource = item.get("resource") or {}
            if not all(resource.get(k) == value for k, value in quote.items()):
                raise MonidError("The approved lookup does not match its saved quote.")
            detail = self.inspect(quote["provider"], quote["endpoint"])
            if (fingerprint(json.dumps(detail, sort_keys=True)) != quote["contract_fingerprint"]
                    or self._price(detail) != quote["quoted_cost_usd"]):
                raise MonidError("The endpoint or price changed. Review a new quote before spending.")
            with self.people.crm_transaction() as conn:
                key = f"monid:dispatch:{quote['quote_id']}"
                if conn.execute("SELECT 1 FROM events WHERE external_key=?", (key,)).fetchone():
                    raise MonidError("This quote was already attempted. It cannot start another paid run.")
                conn.execute("""INSERT INTO events(event_id,person_id,external_key,source,kind,summary,payload,actor,session_id,run_id,tool)
                    VALUES(?,?,?,'sourcecado','research_dispatch','Approved Monid lookup dispatched',?,'director',?,?,'monid_research')""",
                    ("event_"+secrets.token_hex(16), quote["person_id"], key, json.dumps({"quote_id":quote["quote_id"],"approval_id":approval_id,"outcome":"unknown"}),session_id,run_id))
            dispatched = True
            result = self._request("POST", "/v1/run", payload={"provider": quote["provider"], "endpoint": quote["endpoint"], "input": quote["input"]},
                                   paid=True, expected_credential=quote["credential_fingerprint"])
            return True, self._record_result(quote, item, result, run_id=run_id)
        except MonidError as exc:
            return False, {"error": str(exc), "code": "monid_refused"}
        except MonidOutcomeUnknown:
            profile = self.secrets.get("monid") or {}
            self.secrets.replace_if_unchanged({"monid": profile}, {"monid": {**profile, "spend_paused": True}})
            raise
        except Exception:
            if dispatched:
                profile = self.secrets.get("monid") or {}
                self.secrets.replace_if_unchanged({"monid": profile}, {"monid": {**profile,"spend_paused":True}})
                raise MonidOutcomeUnknown("The dispatched lookup could not be recorded reliably. Review the provider run; do not retry automatically.") from None
            return False,{"error":"The current endpoint contract could not be verified. No research run was started.","code":"monid_refused"}

    def _record_result(self, quote, item, data, *, run_id):
        if not isinstance(data, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", str(data.get("runId") or "")):
            raise MonidOutcomeUnknown("Monid returned no verifiable run reference. Do not start another lookup automatically.")
        if data.get("provider") != quote["provider"] or data.get("endpoint") != quote["endpoint"]:
            raise MonidOutcomeUnknown("The returned run does not match the approved endpoint.")
        status = data.get("status")
        running = status in {"READY", "RUNNING", "STOPPING"}
        reported = (data.get("billing") or {}).get("reportedCost")
        actual = None
        if isinstance(reported, dict) and reported.get("currency") == "USD":
            factor = {"MICRO_DOLLAR": Decimal("0.000001"), "CENT": Decimal("0.01"), "DOLLAR": Decimal(1)}.get(reported.get("unit"))
            if factor is not None and type(reported.get("value")) in {int, float} and reported["value"] >= 0:
                amount = Decimal(str(reported["value"])) * factor
                if amount.is_finite(): actual = str(amount.normalize())
        okay = status == "COMPLETED" and (data.get("providerResponse") or {}).get("httpStatus", 500) < 400
        output = {"person_id": quote["person_id"], "provider": "Monid", "remote_provider": quote["provider"],
                  "endpoint":quote["endpoint"], "provider_run_id":data["runId"], "approval_id":item["id"],
                  "run_id":run_id, "quoted_cost_usd":quote["quoted_cost_usd"], "max_cost_usd":quote["max_cost_usd"],
                  "actual_cost_usd": actual, "cost_unknown":actual is None, "untrusted":True,
                  "status":"running" if running else "completed" if okay else "failed",
                  "over_budget":actual is not None and Decimal(actual)>money(quote["max_cost_usd"]),
                  "output":self._safe(data.get("output")) if not running else None}
        # Each status refresh is its own immutable, person/run-bound evidence.
        identity = fingerprint(json.dumps(output, sort_keys=True))
        source = self.people.upsert_attachment(quote["person_id"], record_type="source_ref",
            fields={"provider":"Monid","provider_run_id":data["runId"],"endpoint":quote["endpoint"],
                    "title":f"Monid {quote['provider']} {quote['endpoint']} run {data['runId']}"},
            idempotency_key=f"monid:run:{data['runId']}:{identity}", actor="director", rationale_summary="Read approved Monid research source.",
            session_id=quote["session_id"],run_id=run_id)
        artifact = self.people.upsert_attachment(quote["person_id"], record_type="artifact", fields={"artifact_type":"monid_research","title":f"Monid research: {quote['provider']} {quote['endpoint']}, {output['status']}",**output,"source_ref_id":source["id"]},
            idempotency_key=f"monid:result:{item['id']}:{identity}",actor="director",rationale_summary="Saved untrusted Monid research; identity unchanged.",
            session_id=quote["session_id"],run_id=run_id)
        if output["over_budget"]:
            profile = self.secrets.get("monid") or {}
            self.secrets.replace_if_unchanged({"monid": profile}, {"monid": {**profile, "spend_paused": True}})
        return {**output,"source_ref_id":source["id"],"artifact_id":artifact["id"]}

    def saved_results(self, pid):
        """Saved research for one person, newest first; local read only, never calls Monid."""
        person = self.people.get(pid, expand_sources=True)
        if person is None:
            raise MonidError("This person is unavailable.")
        return saved_research(person)

    def read_result(self, pid, approval_id):
        item = self.inbox.get(approval_id) or {}
        quote = item.get("resource") or {}
        if item.get("name") != "monid_research" or quote.get("person_id") != pid:
            raise MonidError("This research run does not belong to the selected person.")
        if self.people.get(pid) is None or quote.get("credential_fingerprint") != fingerprint(self._profile()["api_key"]):
            raise MonidError("Reconnect the workspace that owns this person’s research result.")
        prior = item.get("execution_result") or {}
        rid = prior.get("provider_run_id")
        if not rid or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", str(rid)):
            raise MonidError("No confirmed Monid run reference is saved. An uncertain attempt cannot be automatically retried.")
        # Read only: never call /v1/run from result refresh, including after restart.
        result = self._request("GET", f"/v1/runs/{rid}", expected_credential=quote["credential_fingerprint"])
        return self._record_result(quote,item,result,run_id=prior.get("run_id"))
