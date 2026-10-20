"""Conservative email interpretations over TaskService's caller-owned writers.

The model identifies exact request spans. This layer authorizes effects using
the attributed original message, never model confidence or model dates. It is
intentionally narrow English grammar; unsupported language remains reviewable.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
import re
import uuid
from zoneinfo import ZoneInfo

from coworker.crm_sources import source_ids, sources_visible
from coworker.crm_tasks import TaskService, validate_timezone

MAX_CANDIDATES = 12
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
CALENDAR_TOKEN = r"(?:\d{4}-\d{2}-\d{2}|today|tomorrow|" + "|".join(WEEKDAYS) + r")"
DATE_TOKEN = re.compile(r"\b" + CALENDAR_TOKEN + r"\b", re.I)
DEADLINE_CLAUSE = re.compile(r"\b(?:by|on|due(?:\s+on)?)\s+(" + CALENDAR_TOKEN + r")[.!?]?\s*$", re.I)
UNCERTAIN = re.compile(r"\b(?:if|unless|maybe|perhaps|might|sometime|eventually|whenever|not|never|don't|do not|no longer|cancel|instead|or|next|later|soon|someday|around|approximately)\b", re.I)
CORRECTION = re.compile(r"\b(?:actually|disregard|scratch|ignore|forget|retract(?:ed)?|withdraw(?:n)?|already)\b|\bno\s+further\b", re.I)
UNSUPPORTED_DATE = re.compile(r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december|week|month|year|weekend|tonight|yesterday|days?|weeks?|months?|\d{1,2}[/\-]\d{1,2})\b", re.I)
UNRESOLVED_TIME = re.compile(r"\b(?:noon|midnight|fortnight|\d+(?:st|nd|rd|th)|\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm))\b", re.I)
ACTIONS = r"send|share|provide|prepare|schedule|review|confirm|arrange"
DIRECT = re.compile(r"^(?:please|can you|could you|would you)\s+(" + ACTIONS + r")\b", re.I)
STOP = set("a an the our your their his her us we i you me for to of with on by in at please can could would team student students project projects this that some sometime nice it be see want like send sent share shared provide prepare schedule review confirm arrange today tomorrow".split()) | set(WEEKDAYS)
OBJECT_FILLER = set("a an the our your their his her us we i you me for to of with please team this that".split())
DELIVERABLE_VARIANTS = {"original", "revised", "updated", "final", "draft", "initial", "first", "second", "third"}


def original_body(body: str) -> str:
    """Mask quotes/signatures without changing character offsets in the source."""
    cutoff = re.search(r"(?im)^On .+wrote:|^-{2,}\s*(?:Original|Forwarded) message|^--\s*$", body)
    original = body if cutoff is None else body[:cutoff.start()] + " " * (len(body) - cutoff.start())
    return "".join(" " * len(line) if line.lstrip().startswith(">") else line for line in original.splitlines(keepends=True))


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z]+", DATE_TOKEN.sub("", text.lower())) if w not in STOP}


def _message_day(message: dict, timezone: str) -> date | None:
    if message.get("received_at_trusted") is not True or not isinstance(message.get("received_at"), str):
        return None
    try:
        moment = datetime.fromisoformat(message["received_at"].replace("Z", "+00:00"))
        return moment.astimezone(ZoneInfo(timezone)).date() if moment.tzinfo is not None else None
    except ValueError:
        return None


def grounded_date(text: str, message: dict, timezone: str) -> tuple[bool, str | None]:
    """One date meaning, grounded in Gmail internalDate and saved IANA zone."""
    if (UNCERTAIN.search(text) or UNSUPPORTED_DATE.search(DATE_TOKEN.sub("", text))
            or UNRESOLVED_TIME.search(text)):
        return False, None
    # Possessive weekdays describe the work's subject, not a deadline.
    text = re.sub(r"\b(?:" + "|".join(WEEKDAYS) + r")[’']s\b", "", text, flags=re.I)
    for clause in re.finditer(r"\b(?:by|on|at|before|after|due|deadline|until|within)\b([^.!?]*)", text, re.I):
        if not DATE_TOKEN.search(clause[1]):
            return False, None
    matches = list(DATE_TOKEN.finditer(text))
    tokens = [m[0] for m in matches]
    if not tokens:
        return True, None
    # A weekday is a deadline only in a supported clause. Its position at the
    # end cannot distinguish "by Friday" from "the file named Friday".
    # Today/tomorrow can also be explicit final temporal adverbs.
    explicit = DEADLINE_CLAUSE.search(text)
    relative_adverb = re.search(r"\s+(today|tomorrow)[.!?]?\s*$", text, re.I)
    if (len(matches) != 1 or (explicit is None and relative_adverb is None)
            or text[matches[0].end():].strip(" .!?\n\t")):
        return False, None
    dates = set()
    day = _message_day(message, timezone)
    for token in tokens:
        token = token.lower()
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
                value = date.fromisoformat(token)
            elif day is None:
                return False, None
            elif token in {"today", "tomorrow"}:
                value = day + timedelta(days=token == "tomorrow")
            else:
                offset = (WEEKDAYS.index(token) - day.weekday()) % 7
                # Bare same-day weekday could mean today or the next week.
                if offset == 0:
                    return False, None
                value = day + timedelta(days=offset)
        except ValueError:
            return False, None
        dates.add(value.isoformat())
    return (True, next(iter(dates))) if len(dates) == 1 else (False, None)


def obligation(text: str) -> tuple[str, tuple[str, ...]] | None:
    """The supported action and object, retaining deliverable modifiers.

    This is a conservative matching aid, separate from inference suppression.
    It never treats model titles or a stripped token subset as the promise.
    """
    action = re.match(r"(?:(?:please|can you|could you|would you)\s+)?(" + ACTIONS + r")\b\s*(.+)", text.strip(), re.I)
    if action is None:
        return None
    content = action[2]
    deadline = DEADLINE_CLAUSE.search(content)
    if deadline:
        content = content[:deadline.start()]
    else:
        content = re.sub(r"\s+(?:today|tomorrow)[.!?]?\s*$", "", content, flags=re.I)
    words = tuple(w.rstrip("s") for w in re.findall(r"[a-z0-9]+", content.lower()) if w not in OBJECT_FILLER)
    return (action[1].lower(), words) if words else None


def _request_span(original: str, citation: str, title: str) -> tuple[int, int] | None:
    """Canonical source sentence, independent of model punctuation/expansion.

    Interpret the full containing sentence, even when the model cites only its
    actionable middle. Expanded citations select the sentence about this work.
    No sentence text or model title enters the persisted suppression identity.
    """
    if original.count(citation) != 1:
        return None
    begin = original.index(citation)
    finish = begin + len(citation)
    spans, cursor = [], 0
    for boundary in re.finditer(r"[.!?](?=\s|$)|\n", original):
        end = boundary.end()
        start = cursor
        while start < end and original[start].isspace():
            start += 1
        if start < end and start < finish and end > begin:
            spans.append((start, end))
        cursor = end
    start = cursor
    while start < len(original) and original[start].isspace():
        start += 1
    if start < len(original) and start < finish:
        spans.append((start, len(original.rstrip())))
    clauses = []
    for start, end in spans:
        cursor = start
        for separator in re.finditer(r";\s*|\s+and\s+(?=(?:(?:please|can you|could you|would you)\s+)?(?:" + ACTIONS + r")\b)", original[start:end], re.I):
            clause_end = start + separator.start()
            if cursor < finish and clause_end > begin:
                clauses.append((cursor, clause_end))
            cursor = start + separator.end()
        if cursor < finish and end > begin:
            clauses.append((cursor, end))
    return max(clauses, key=lambda span: len(_words(original[span[0]:span[1]]) & _words(title)), default=None)


def validate_candidates(output: object, keys: set[str]) -> list[dict]:
    if not isinstance(output, list) or len(output) > MAX_CANDIDATES:
        raise ValueError("invalid_model_output")
    for item in output:
        if (not isinstance(item, dict) or set(item) - {"kind", "source", "request", "title", "task_id"}
                or item.get("kind") not in {"create", "complete"} or item.get("source") not in keys
                or not isinstance(item.get("request"), str) or not 10 <= len(item["request"]) <= 1000
                or not isinstance(item.get("title"), str) or not 1 <= len(item["title"]) <= 200
                or ("task_id" in item and not isinstance(item["task_id"], str))):
            raise ValueError("invalid_model_output")
    return output


class EmailTaskWriter:
    def __init__(self, people, *, allowed: set[str], timezone: str):
        self.people = people
        self.allowed = allowed
        self.timezone = validate_timezone(timezone)
        self.tasks = TaskService(people)

    def _saved_request(self, task: dict) -> str | None:
        row = self.people._conn.execute("""SELECT p.proposed_value FROM crm_proposals p
            JOIN crm_changes c ON c.change_id=p.change_id WHERE c.entity_type='task'
            AND c.entity_id=? AND p.target_kind='task_create' ORDER BY c.created_at LIMIT 1""", (task["task_id"],)).fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        return value.get("details") if isinstance(value, dict) and isinstance(value.get("details"), str) else None

    def task_relation(self, task: dict, request: str) -> str:
        """same, uncertain, or distinct; source provenance precedes model prose."""
        saved = self._saved_request(task)
        # A protected title correction has current human authority.
        if "title" in task["protected_fields"] and obligation(task["title"]) != obligation(saved or task["title"]):
            saved = None
        incoming, existing = obligation(request), obligation(saved or task["title"])
        if incoming is None or existing is None:
            return "uncertain" if _words(request) & _words(saved or task["title"]) else "distinct"
        action, objects = incoming
        prior_action, prior_objects = existing
        if action != prior_action:
            return "distinct"
        if objects == prior_objects:
            return "same"
        variants = set(objects) & DELIVERABLE_VARIANTS
        prior_variants = set(prior_objects) & DELIVERABLE_VARIANTS
        if variants != prior_variants:
            return "distinct"
        return "uncertain" if set(objects) & set(prior_objects) else "distinct"

    def matching_tasks(self, pid: str, request: str) -> list[tuple[dict, str]]:
        records = [self.tasks._task_dict(row) for row in self.people._conn.execute("SELECT * FROM person_tasks WHERE person_id=?", (pid,))]
        return [(task, relation) for task in records if (relation := self.task_relation(task, request)) != "distinct"]

    def apply(self, pid: str, candidates: list[dict], evidence, revision: int) -> tuple[list, list]:
        """Caller owns BEGIN IMMEDIATE covering descriptions and checkpoint too."""
        manifest = {m["key"]: m for m in evidence.manifest}
        messages = {m["key"]: m for m in evidence.messages}
        changes, proposals = [], []
        for item in candidates:
            support, message = manifest[item["source"]], messages[item["source"]]
            body, request = message["body"], item["request"]
            original = original_body(body)
            span = _request_span(original, request, item["title"])
            if span is None:
                # A missing/quoted/multiply occurring span is not a citation.
                continue
            start, end = span
            request = original[start:end]
            identity = {k: support[k] for k in ("source_id", "message_id", "body_fingerprint")}
            identity.update(start=start, end=end)
            fingerprint = _hash(identity)
            intent = "email-task:" + item["kind"]
            prior = self.people._conn.execute("""SELECT * FROM crm_proposals WHERE person_id=?
                AND intent_key=? AND support_source_fingerprint=?""", (pid, intent, fingerprint)).fetchone()
            if prior:
                if prior["state"] == "pending":
                    proposals.append(prior["proposal_id"])
                continue
            refs = source_ids(self.people, pid, [support["source_id"]], self.allowed)
            # Latest task versions/protection are loaded after model work, under
            # the same write lock. No model snapshot can overwrite a human edit.
            matching = self.matching_tasks(pid, request)
            if item.get("task_id") is not None:
                row = self.people._conn.execute("SELECT * FROM person_tasks WHERE person_id=? AND task_id=?", (pid, item["task_id"])).fetchone()
                if row is None:
                    continue
                hinted = self.tasks._task_dict(row)
                if not sources_visible(self.people, pid, hinted["source_refs"], self.allowed):
                    continue
                relation = self.task_relation(hinted, request)
                # A human paraphrase hint remains reviewable, but it is never
                # enough evidence to move a different obligation's deadline.
                if item["kind"] == "complete":
                    matching = [(hinted, "same")]
                elif relation != "distinct":
                    matching = [(hinted, relation)]
                elif self._saved_request(hinted) is None:
                    incoming, existing = obligation(request), obligation(hinted["title"])
                    if incoming is None or existing is None or not set(incoming[1]) & set(existing[1]):
                        matching = [(hinted, "uncertain")]
            elif item["kind"] == "complete":
                continue
            matches = [task for task, _relation in matching]
            exact_target = len(matching) == 1 and matching[0][1] == "same"
            # A known finished promise is never reopened or recreated by mail.
            if matches and all(t["state"] != "open" for t in matches):
                continue
            target = matches[0] if len(matches) == 1 else None
            if matches and any(not sources_visible(self.people, pid, t["source_refs"], self.allowed) for t in matches):
                continue
            due_clear, due = grounded_date(request, message, self.timezone)
            context_clear = not UNCERTAIN.search(original) and not CORRECTION.search(original)
            due_clear = due_clear and context_clear
            direct = DIRECT.search(request.strip())
            action = direct[1].lower() if direct else None
            if action is None:
                # A second verb can inherit the explicit request's subject only
                # across our deterministic same-sentence conjunction boundary.
                prior_clause = re.split(r"[.!?\n]", original[:start])[-1].strip()
                inherited = re.match(r"(" + ACTIONS + r")\b", request.strip(), re.I)
                if inherited and DIRECT.search(prior_clause) and re.search(r"\band\s*$", prior_clause):
                    action = inherited[1].lower()
            # The entire original context must agree; omitted nearby corrections,
            # dates and conditions cannot be removed by a model's short citation.
            other = original[:start] + original[end:]
            # Other separately cited promises may legitimately have their own date.
            for other_item in candidates:
                if other_item["source"] == item["source"]:
                    other_span = _request_span(original, other_item["request"], other_item["title"])
                    if other_span:
                        other = other.replace(original[other_span[0]:other_span[1]], "")
            clear = (item["kind"] == "create" and support["direction"] == "inbound"
                and action is not None
                and context_clear and not DATE_TOKEN.search(other)
                and due_clear and bool(_words(request)) and _words(item["title"]) <= _words(request))
            if clear:
                clear = re.match(action + r"\b", item["title"].strip(), re.I) is not None
            # Persist a sourced proposal for every interpretation, even automatic
            # work, so dismissal/undo/completion suppresses unchanged support.
            proposal_id, change_id = "proposal_" + uuid.uuid4().hex, None
            kind = "task_update" if matches else "task_create"
            if item["kind"] == "complete":
                value = {"changes": {"state": "completed"}}
                reason = "Possible task fulfillment; no validated task-to-send binding exists. Review the cited message."
            elif matches:
                value = {"changes": {"due_date": due if due_clear else None}, "request": request,
                    "requires_target_review": not exact_target}
                reason = "This request may match saved work. Review the date without replacing human details or state."
            else:
                value = {"title": item["title"], "details": request, "due_date": due if clear else None}
                reason = "A full attributed inbound message explicitly requests this work." if clear else "The cited request or deadline needs director review; no date was inferred."
            if clear and not matches:
                response = self.tasks._create_uncommitted(pid, **value, timezone=self.timezone, actor="assistant",
                    source_refs=refs, allowed_source_ids=self.allowed, operation_id="crm-analysis:" + proposal_id, reason=reason)
                change_id = response["change"]["change_id"]
                changes.append(change_id)
            else:
                proposals.append(proposal_id)
            # Multiple possible matches produce a reconciliation proposal that cannot be
            # accepted without choosing the saved target in the future review UI.
            if len(matches) > 1:
                kind, value = "task_update", {"changes": {}, "requires_target_review": True}
            self.people._conn.execute("""INSERT INTO crm_proposals(proposal_id, person_id, target_kind,
                target_id, proposed_value, expected_target_version, reason, source_refs, intent_key,
                support_source_fingerprint, input_revision, state, resolution, change_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (proposal_id, pid, kind, target["task_id"] if target else None, json.dumps(value),
                 target["version"] if target else 0, reason, json.dumps(refs), intent, fingerprint,
                 revision, "accepted" if change_id else "pending", "automatic" if change_id else None,
                 change_id, self.people._now(), self.people._now()))
            self.people._conn.execute("""INSERT INTO crm_task_evidence
                (proposal_id, source_id, message_id, body_fingerprint, request_start, request_end)
                VALUES (?, ?, ?, ?, ?, ?)""", (proposal_id, refs[0], support["message_id"], support["body_fingerprint"], start, end))
        return changes, proposals
