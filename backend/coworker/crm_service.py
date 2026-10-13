"""Validated manual CRM operations. Connectors and model providers are absent."""

import hashlib
import json
import re
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from coworker.crm_repository import CrmRepository

DEFAULT_TIMEZONE = "America/Los_Angeles"


def valid_timezone(value):
    if not isinstance(value, str) or not value or len(value) > 100:
        raise ValueError("Choose a valid IANA timezone.")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Choose a valid IANA timezone.") from None
    return value


class CrmService:
    def __init__(self, people, settings):
        self.repository = CrmRepository(people)
        self.settings = settings
        if settings.get_setting("director_timezone") is None:
            settings.set_setting("director_timezone", DEFAULT_TIMEZONE)

    def timezone(self):
        return valid_timezone(self.settings.get_setting("director_timezone"))

    def set_timezone(self, timezone):
        self.settings.set_setting("director_timezone", valid_timezone(timezone))
        return {"timezone": timezone}

    def save_task(self, person_id, payload, *, task_id=None):
        allowed = {"operation_id", "title", "details", "due_date"}
        if task_id is not None:
            allowed.add("expected_version")
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError("Unexpected task fields.")
        operation_id = payload.get("operation_id")
        if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", operation_id):
            raise ValueError("A valid save ID is required.")
        version = payload.get("expected_version")
        if task_id is not None and (type(version) is not int or version < 1):
            raise ValueError("A positive task version is required.")
        changes = {k: v for k, v in payload.items() if k in ("title", "details", "due_date")}
        if task_id is None and "title" not in changes:
            raise ValueError("Title is required.")
        if not changes:
            raise ValueError("No task changes supplied.")
        if "title" in changes:
            title = changes["title"]
            if not isinstance(title, str) or not title.strip() or len(title) > 200 or "\x00" in title:
                raise ValueError("Title must contain 1–200 characters.")
            changes["title"] = title.strip()
        if "details" in changes and (not isinstance(changes["details"], str) or
                len(changes["details"]) > 8000 or "\x00" in changes["details"]):
            raise ValueError("Details must contain at most 8,000 characters.")
        if changes.get("due_date") is not None:
            due = changes["due_date"]
            if not isinstance(due, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
                raise ValueError("Due date must be a valid YYYY-MM-DD calendar date.")
            try:
                date.fromisoformat(due)
            except ValueError:
                raise ValueError("Due date must be a valid YYYY-MM-DD calendar date.") from None
        # Fingerprint the intent, not the timezone preference at retry time.
        intent = {"person_id": person_id, "task_id": task_id, "expected_version": version, "changes": changes}
        fingerprint = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
        return self.repository.save(person_id=person_id, task_id=task_id, operation_id=operation_id,
            fingerprint=fingerprint, changes=changes, expected_version=version, timezone=self.timezone())
