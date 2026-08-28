from pathlib import Path

import pytest


def test_meeting_evidence_domain_module_exists():
    module = Path(__file__).parents[1] / "coworker" / "meeting_evidence.py"

    assert module.is_file()


def _person(people, *, apollo_id, first, last, email=None):
    person = people.keep_from_apollo(
        apollo_id=apollo_id,
        first_name=first,
        last_name_obfuscated=last,
        title="Founder",
        company="Analytic",
    )
    if email:
        people.apply_enrichment(person["person_id"], email=email)
    return people.get(person["person_id"])


def _calendar_event(
    *,
    event_id="cal-1",
    title="Dinner with Ada",
    attendees=None,
    start="2026-09-01T18:00:00-07:00",
):
    return {
        "id": event_id,
        "summary": title,
        "start": {"dateTime": start},
        "end": {"dateTime": "2026-09-01T19:00:00-07:00"},
        "attendees": list(attendees or []),
        "htmlLink": f"https://calendar.test/{event_id}",
    }


def _granola_meeting(
    *,
    meeting_id="granola-1",
    title="Ada follow-up",
    participants=None,
    notes="Discussed partnership timing.",
):
    return {
        "id": meeting_id,
        "title": title,
        "startTime": "2026-09-02T10:00:00Z",
        "endTime": "2026-09-02T10:30:00Z",
        "participants": list(participants or []),
        "url": f"https://granola.test/{meeting_id}",
        "notes": notes,
    }


def test_exact_unique_email_auto_attaches_stable_calendar_evidence(tmp_path):
    from coworker.meeting_evidence import MeetingEvidenceStore
    from coworker.people import PersonStore

    people = PersonStore(tmp_path)
    ada = _person(
        people,
        apollo_id="ada",
        first="Ada",
        last="Lovelace",
        email="ADA@ANALYTIC.EXAMPLE",
    )
    store = MeetingEvidenceStore(tmp_path, people=people)

    result = store.refresh(
        calendar_fetch=lambda: {
            "events": [
                _calendar_event(
                    attendees=[
                        {"email": "ada@analytic.example", "displayName": "Ada Lovelace"}
                    ]
                )
            ]
        }
    )

    assert result["sources"]["calendar"] == {"status": "ok", "records": 1}
    attached = store.for_person(ada["person_id"])["attached"]
    assert len(attached) == 1
    meeting = attached[0]
    assert meeting["provider"] == "calendar"
    assert meeting["provider_id"] == "cal-1"
    assert meeting["starts_at"] == "2026-09-01T18:00:00-07:00"
    assert meeting["participants"] == [
        {"name": "Ada Lovelace", "email": "ada@analytic.example"}
    ]
    assert meeting["source_ref"] == {
        "id": "calendar:cal-1",
        "title": "Dinner with Ada",
        "url": "https://calendar.test/cal-1",
        "provider": "Google Calendar",
    }
    assert meeting["status"] == "attached"
    assert meeting["match_reason"] == "exact_email"
    timeline = people.timeline(ada["person_id"])
    assert len(timeline) == 1
    assert timeline[0]["source"] == "calendar"
    assert timeline[0]["kind"] == "meeting"


def test_name_only_match_is_proposed_and_never_auto_attached(tmp_path):
    from coworker.meeting_evidence import MeetingEvidenceStore
    from coworker.people import PersonStore

    people = PersonStore(tmp_path)
    ada = _person(people, apollo_id="ada", first="Ada", last="Lovelace")
    store = MeetingEvidenceStore(tmp_path, people=people)

    store.refresh(
        granola_fetch=lambda: {
            "meetings": [
                _granola_meeting(participants=[{"name": "Ada Lovelace"}])
            ]
        }
    )

    view = store.for_person(ada["person_id"])
    assert view["attached"] == []
    assert len(view["proposed"]) == 1
    assert view["proposed"][0]["match_reason"] == "name_only"
    assert people.timeline(ada["person_id"]) == []


def test_conflicting_and_multi_person_matches_are_all_review_only(tmp_path):
    from coworker.meeting_evidence import MeetingEvidenceStore
    from coworker.people import PersonStore

    people = PersonStore(tmp_path)
    ada = _person(
        people,
        apollo_id="ada",
        first="Ada",
        last="Lovelace",
        email="ada@example.test",
    )
    grace = _person(
        people,
        apollo_id="grace",
        first="Grace",
        last="Hopper",
        email="grace@example.test",
    )
    store = MeetingEvidenceStore(tmp_path, people=people)

    store.refresh(
        calendar_fetch=lambda: {
            "events": [
                _calendar_event(
                    attendees=[
                        {"email": "ada@example.test", "displayName": "Grace Hopper"},
                        {"email": "grace@example.test", "displayName": "Grace Hopper"},
                    ]
                )
            ]
        }
    )

    assert store.for_person(ada["person_id"])["attached"] == []
    assert store.for_person(grace["person_id"])["attached"] == []
    assert len(store.for_person(ada["person_id"])["proposed"]) == 1
    assert len(store.for_person(grace["person_id"])["proposed"]) == 1
    assert people.timeline(ada["person_id"]) == []
    assert people.timeline(grace["person_id"]) == []


def test_director_can_attach_or_reject_a_proposed_meeting(tmp_path):
    from coworker.meeting_evidence import MeetingEvidenceStore
    from coworker.people import PersonStore

    people = PersonStore(tmp_path)
    ada = _person(people, apollo_id="ada", first="Ada", last="Lovelace")
    grace = _person(people, apollo_id="grace", first="Grace", last="Hopper")
    store = MeetingEvidenceStore(tmp_path, people=people)
    store.refresh(
        granola_fetch=lambda: {
            "meetings": [
                _granola_meeting(
                    participants=[{"name": "Ada Lovelace"}], notes="Useful notes"
                ),
                _granola_meeting(
                    meeting_id="granola-2",
                    title="Grace follow-up",
                    participants=[{"name": "Grace Hopper"}],
                ),
            ]
        }
    )
    ada_proposal = store.for_person(ada["person_id"])["proposed"][0]
    grace_proposal = store.for_person(grace["person_id"])["proposed"][0]

    attached = store.attach(ada_proposal["evidence_id"], ada["person_id"])
    rejected = store.reject(grace_proposal["evidence_id"], grace["person_id"])

    assert attached["status"] == "attached"
    assert rejected["status"] == "rejected"
    assert len(store.for_person(ada["person_id"])["attached"]) == 1
    assert store.for_person(ada["person_id"])["proposed"] == []
    assert len(store.for_person(grace["person_id"])["rejected"]) == 1
    assert people.timeline(grace["person_id"]) == []


def test_duplicate_refresh_updates_record_and_timeline_without_duplication(tmp_path):
    from coworker.meeting_evidence import MeetingEvidenceStore
    from coworker.people import PersonStore

    people = PersonStore(tmp_path)
    ada = _person(
        people,
        apollo_id="ada",
        first="Ada",
        last="Lovelace",
        email="ada@example.test",
    )
    store = MeetingEvidenceStore(tmp_path, people=people)
    payload = {
        "events": [
            _calendar_event(
                title="Initial title",
                attendees=[{"email": "ada@example.test", "displayName": "Ada Lovelace"}],
            )
        ]
    }
    store.refresh(calendar_fetch=lambda: payload)
    first = store.for_person(ada["person_id"])["attached"][0]
    first_event = people.timeline(ada["person_id"])[0]
    payload["events"][0]["summary"] = "Updated title"

    store.refresh(calendar_fetch=lambda: payload)

    second = store.for_person(ada["person_id"])["attached"][0]
    timeline = people.timeline(ada["person_id"])
    assert second["evidence_id"] == first["evidence_id"]
    assert second["title"] == "Updated title"
    assert len(timeline) == 1
    assert timeline[0]["event_id"] == first_event["event_id"]
    assert timeline[0]["summary"] == "Calendar meeting: Updated title"


def test_calendar_and_granola_failures_are_independent(tmp_path):
    from coworker.meeting_evidence import MeetingEvidenceStore
    from coworker.people import PersonStore

    people = PersonStore(tmp_path)
    ada = _person(
        people,
        apollo_id="ada",
        first="Ada",
        last="Lovelace",
        email="ada@example.test",
    )
    store = MeetingEvidenceStore(tmp_path, people=people)

    result = store.refresh(
        calendar_fetch=lambda: (_ for _ in ()).throw(RuntimeError("missing scope")),
        granola_fetch=lambda: {
            "meetings": [
                _granola_meeting(
                    participants=[
                        {"email": "ada@example.test", "name": "Ada Lovelace"}
                    ]
                )
            ]
        },
    )

    assert result["sources"]["calendar"]["status"] == "failed"
    assert result["sources"]["calendar"]["error"] == "unavailable"
    assert result["sources"]["granola"] == {"status": "ok", "records": 1}
    assert len(store.for_person(ada["person_id"])["attached"]) == 1


def test_restart_and_cross_person_isolation(tmp_path):
    from coworker.meeting_evidence import MeetingEvidenceStore
    from coworker.people import PersonStore

    people = PersonStore(tmp_path)
    ada = _person(
        people,
        apollo_id="ada",
        first="Ada",
        last="Lovelace",
        email="ada@example.test",
    )
    grace = _person(
        people,
        apollo_id="grace",
        first="Grace",
        last="Hopper",
        email="grace@example.test",
    )
    MeetingEvidenceStore(tmp_path, people=people).refresh(
        calendar_fetch=lambda: {
            "events": [
                _calendar_event(
                    attendees=[{"email": "ada@example.test", "displayName": "Ada Lovelace"}]
                )
            ]
        }
    )

    restarted_people = PersonStore(tmp_path)
    restarted = MeetingEvidenceStore(tmp_path, people=restarted_people)
    assert len(restarted.for_person(ada["person_id"])["attached"]) == 1
    assert restarted.for_person(grace["person_id"])["attached"] == []
    assert len(restarted_people.timeline(ada["person_id"])) == 1
    assert restarted_people.timeline(grace["person_id"]) == []


def test_unknown_provider_and_malformed_records_fail_without_writing(tmp_path):
    from coworker.meeting_evidence import MeetingEvidenceStore
    from coworker.people import PersonStore

    people = PersonStore(tmp_path)
    store = MeetingEvidenceStore(tmp_path, people=people)

    with pytest.raises(ValueError, match="provider"):
        store.ingest("calendar_write", [])
    with pytest.raises(ValueError, match="provider id"):
        store.ingest("calendar", [{"summary": "missing id"}])
    assert people.list_people() == []
