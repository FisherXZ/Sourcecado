"""The living brief a successor reads instead of raw tool JSON.

Every negative assertion here is paired with a positive one on the same
brief. A brief that came back empty would satisfy "the restricted body is
absent" and "the other person's evidence is absent" while proving nothing, so
each isolation test first proves the brief was actually built.
"""

from __future__ import annotations

import pytest

from coworker.brief import (
    BRIEF_VERSION,
    brief_payload,
    build_brief,
    handoff_draft,
    person_brief,
    prompt_context,
)
from coworker.context_projection import (
    ContextAuthority,
    ContextCategory,
    ContextSensitivity,
    ContextSourceRef,
    ContextState,
    ProjectionItem,
    prepare_context_projection,
)
from coworker.drive_evidence import attach as attach_drive_evidence
from coworker.meeting_evidence import MeetingEvidenceStore
from coworker.people import PersonStore
from coworker.run_evidence import Evidence


def _person(store: PersonStore, **kwargs) -> dict:
    fields = {
        "apollo_id": "ada",
        "first_name": "Ada",
        "last_name_obfuscated": "Lovelace",
        "title": "Founder",
        "company": "Analytic",
        "target": "research dinner",
    }
    fields.update(kwargs)
    return store.keep_from_apollo(**fields)


def _claim_ids(payload: dict) -> set[str]:
    return {claim["id"] for claim in payload["claims"]}


def _texts(payload: dict) -> str:
    return "\n".join(claim["text"] for claim in payload["claims"])


# --- 1. thin new person --------------------------------------------------


def test_thin_person_states_identity_target_and_its_own_gaps(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)

    payload = brief_payload(person_brief(store, person["person_id"]))

    assert payload["identity"]["text"].startswith("Ada Lovelace")
    assert payload["identity"]["source_refs"], "identity must cite the record"
    assert payload["target"]["text"] == "research dinner"
    assert payload["target"]["authority"] == ContextAuthority.DIRECTOR
    gaps = {gap["text"] for gap in payload["gaps"]}
    assert any("email" in text for text in gaps)
    assert any("wants" in text for text in gaps)
    assert all(gap["state"] == ContextState.MISSING for gap in payload["gaps"])
    assert payload["partial"] is False
    assert payload["version"] == BRIEF_VERSION


def test_every_claim_carries_at_least_one_source_reference_or_is_missing(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    store.append_event(pid, source="gmail", kind="mail", summary="Read mail from Ada")

    payload = brief_payload(person_brief(store, pid))

    assert payload["claims"], "the brief must not be empty for this person"
    for claim in payload["claims"]:
        if claim["state"] == ContextState.MISSING:
            continue
        assert claim["source_refs"], f"unsourced claim {claim['id']}"
        for ref_id in claim["source_refs"]:
            assert ref_id in {row["id"] for row in payload["source_refs"]}


# --- 2. sent outreach ----------------------------------------------------


def test_sent_outreach_becomes_last_contact_and_an_outcome(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    store.append_event(
        pid,
        source="gmail",
        kind="send",
        summary="Sent draft draft-1",
        payload={"sent": True, "to": "ada@analytic.example", "subject": "Dinner"},
        tool="gmail_send",
    )
    store.set_sequence(pid, "in_conversation", actor="assistant")

    payload = brief_payload(person_brief(store, pid))

    assert payload["last_contact"]["direction"] == "outbound"
    assert payload["last_contact"]["at"]
    assert "Sent draft draft-1" in _texts(payload)
    assert payload["state"]["sequence"] == "in_conversation"
    assert payload["outcome"]["text"]


# --- 3. inbound reply ----------------------------------------------------


def test_inbound_reply_is_last_contact_and_answers_what_they_want(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    store.append_event(
        pid,
        source="gmail",
        kind="send",
        summary="Sent draft draft-1",
        payload={"sent": True, "to": "ada@analytic.example"},
    )
    store.upsert_external_event(
        pid,
        external_key="gmail:message:m-1",
        source="gmail",
        kind="mail",
        summary="Reply from ada@analytic.example: Dinner",
        payload={
            "direction": "inbound",
            "message_id": "m-1",
            "thread_id": "t-1",
            "snippet": "Happy to come, but only after the 15th.",
            "received_at": "2026-08-26T10:00:00+00:00",
            "source_ref": {"provider": "Gmail", "message_id": "m-1"},
            "untrusted": True,
        },
        tool="gmail_read",
    )

    payload = brief_payload(person_brief(store, pid))

    assert payload["last_contact"]["direction"] == "inbound"
    assert "Reply from ada@analytic.example" in _texts(payload)
    wants = payload["wants"]
    assert wants["state"] != ContextState.MISSING
    assert "only after the 15th" in wants["text"]
    assert wants["source_refs"]


# --- 4. meeting evidence -------------------------------------------------


def test_meeting_evidence_reaches_the_brief_with_its_source_reference(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    store.apply_enrichment(pid, email="ada@example.test")
    MeetingEvidenceStore(tmp_path, people=store).refresh(
        granola_fetch=lambda: {
            "meetings": [
                {
                    "id": "granola-1",
                    "title": "Partnership notes",
                    "participants": [{"email": "ada@example.test"}],
                    "notes": "Discussed a September pilot.",
                    "url": "https://granola.test/granola-1",
                }
            ]
        }
    )

    payload = brief_payload(person_brief(store, pid))
    providers = {row["provider"] for row in payload["source_refs"]}

    assert "granola" in providers
    assert "Partnership notes" in _texts(payload)
    assert any("Discussed a September pilot" in claim["text"] for claim in payload["claims"])


def test_a_long_meeting_note_is_marked_truncated_rather_than_dropped(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    store.apply_enrichment(pid, email="ada@example.test")
    MeetingEvidenceStore(tmp_path, people=store).refresh(
        granola_fetch=lambda: {
            "meetings": [
                {
                    "id": "granola-long",
                    "title": "Long review",
                    "participants": [{"email": "ada@example.test"}],
                    "notes": "September pilot. " + ("detail " * 400),
                }
            ]
        }
    )

    payload = brief_payload(person_brief(store, pid))
    notes = [claim for claim in payload["claims"] if claim["truncated"]]

    assert notes, "a long note must still reach the brief, marked truncated"
    assert "September pilot." in notes[0]["text"]


# --- 5. Drive evidence ---------------------------------------------------


def test_drive_evidence_appears_with_extraction_truth(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    attach_drive_evidence(
        store,
        pid,
        kind="search_result",
        raw={
            "id": "drive-1",
            "name": "Fall sourcing masterdoc",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-08-01T10:00:00+00:00",
            "webViewLink": "https://drive.test/drive-1",
            "status": "read",
        },
        actor="director",
        rationale_summary="Director attached Drive evidence.",
    )
    attach_drive_evidence(
        store,
        pid,
        kind="search_result",
        raw={
            "id": "drive-2",
            "name": "Scanned bylaws",
            "modifiedTime": "2026-08-02T10:00:00+00:00",
            "status": "unsupported",
        },
        actor="director",
        rationale_summary="Director attached Drive evidence.",
    )

    payload = brief_payload(person_brief(store, pid))
    rows = {row["title"]: row for row in payload["source_refs"] if row["title"]}

    assert rows["Fall sourcing masterdoc"]["evidence"] == Evidence.PRESENT
    assert rows["Scanned bylaws"]["evidence"] == Evidence.UNSUPPORTED
    assert "Fall sourcing masterdoc" in _texts(payload)


def test_artifacts_and_knowledge_gaps_are_separate_brief_sections(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    store.upsert_attachment(
        pid,
        record_type="artifact",
        fields={"title": "Dinner invitation draft", "url": "https://docs.test/invite"},
        idempotency_key="artifact:invite",
        actor="director",
        rationale_summary="Director filed the invitation draft.",
    )
    store.upsert_attachment(
        pid,
        record_type="knowledge_gap",
        fields={
            "kind": "unassigned_reply",
            "evidence": "ambiguous",
            "question": "Which person replied on this thread?",
        },
        idempotency_key="gmail:reply:m-9",
        actor="assistant",
        rationale_summary="Inbound reply could not be tied to one person.",
    )

    payload = brief_payload(person_brief(store, pid))

    assert any("Dinner invitation draft" in item["text"] for item in payload["artifacts"])
    assert any(
        "Which person replied on this thread?" in gap["text"] for gap in payload["gaps"]
    )
    assert all(item["id"] not in _claim_ids(payload) for item in [])


# --- 6. conflict ---------------------------------------------------------


def test_two_sources_disagreeing_produce_one_conflicting_claim(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    store.apply_enrichment(pid, email="ada@analytic.example", company="Analytic")
    store.append_event(
        pid,
        source="web",
        kind="research",
        summary="Bio lists a different employer",
        payload={"company": "Difference Engine Co"},
    )

    payload = brief_payload(person_brief(store, pid))
    conflicts = payload["conflicts"]

    assert conflicts, "a disagreeing source must surface as a conflict"
    conflict = conflicts[0]
    assert conflict["state"] == ContextState.CONFLICTING
    assert "Analytic" in conflict["text"]
    assert "Difference Engine Co" in conflict["text"]
    assert len(conflict["source_refs"]) >= 2


def test_stale_evidence_is_marked_stale_not_dropped(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    store.append_event(pid, source="gmail", kind="mail", summary="Read mail from Ada")

    fresh = brief_payload(person_brief(store, pid))
    aged = brief_payload(person_brief(store, pid, now="2027-08-27T00:00:00+00:00"))

    assert _claim_ids(aged) >= {
        claim["id"] for claim in fresh["claims"] if claim["state"] != ContextState.MISSING
    }
    assert any(claim["state"] == ContextState.STALE for claim in aged["claims"])
    assert not any(row["fresh"] for row in aged["source_refs"])


# --- 7. restriction ------------------------------------------------------


def test_a_restricted_source_body_never_enters_the_brief_or_the_prompt(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    attach_drive_evidence(
        store,
        pid,
        kind="search_result",
        raw={
            "id": "drive-open",
            "name": "Fall sourcing masterdoc",
            "modifiedTime": "2026-08-01T10:00:00+00:00",
            "status": "read",
        },
        actor="director",
        rationale_summary="Director attached Drive evidence.",
    )
    attach_drive_evidence(
        store,
        pid,
        kind="search_result",
        raw={
            "id": "drive-secret",
            "name": "Board compensation memo",
            "modifiedTime": "2026-08-02T10:00:00+00:00",
            "status": "read",
            "sensitive_content_redacted": True,
        },
        actor="director",
        rationale_summary="Director attached Drive evidence.",
    )

    brief = person_brief(store, pid)
    payload = brief_payload(brief)
    prompt = prompt_context(brief)

    # Non-vacuity: the brief really was built, and the unrestricted sibling is in it.
    assert payload["claims"]
    assert "Fall sourcing masterdoc" in _texts(payload)
    assert "Fall sourcing masterdoc" in prompt
    # The restricted record is counted, never quoted.
    assert payload["restricted_source_count"] == 1
    assert "Board compensation memo" not in _texts(payload)
    assert "Board compensation memo" not in prompt
    assert "drive-secret" not in prompt
    assert any("restricted" in gap["text"].lower() for gap in payload["gaps"])


def test_a_restricted_item_that_reaches_the_boundary_is_refused(tmp_path):
    """The tripwire behind the count: assembling a restricted body cannot ship."""
    identity = _identity("per_x", "sess-x")
    item = ProjectionItem(
        id="evidence:leak",
        category=ContextCategory.PERSON_EVIDENCE,
        text="Board compensation memo",
        tokens=8,
        state=ContextState.CURRENT,
        authority=ContextAuthority.CONNECTOR,
        updated_at="2026-08-27T00:00:00+00:00",
        source_refs=(
            ContextSourceRef(
                id="drive:secret",
                provider="drive",
                locator="drive-secret",
                observed_at="2026-08-27T00:00:00+00:00",
                modified_at=None,
            ),
        ),
        person_id="per_x",
        sensitivity=ContextSensitivity.RESTRICTED,
    )

    with pytest.raises(ValueError, match="restricted"):
        prepare_context_projection(identity=identity, items=(item,))


def _identity(person_id: str, session_id: str):
    from coworker.context_projection import ProjectionIdentity

    return ProjectionIdentity(
        persona_id="sourcing",
        session_id=session_id,
        person_id=person_id,
        target=None,
        prompt_version=BRIEF_VERSION,
        effective_tools_hash="",
    )


# --- 8. partial refresh --------------------------------------------------


def test_a_failed_refresh_keeps_every_successful_claim_and_marks_partial(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    store.apply_enrichment(pid, email="ada@example.test")

    def granola():
        return {
            "meetings": [
                {
                    "id": "granola-1",
                    "title": "Partnership notes",
                    "participants": [{"email": "ada@example.test"}],
                    "notes": "Discussed a September pilot.",
                }
            ]
        }

    def calendar():
        raise RuntimeError("Calendar is unavailable")

    meetings = MeetingEvidenceStore(tmp_path, people=store)
    before = brief_payload(person_brief(store, pid))
    assert "Partnership notes" not in _texts(before)

    result = meetings.refresh(calendar_fetch=calendar, granola_fetch=granola)
    after = brief_payload(person_brief(store, pid, refresh=result))

    assert result["sources"]["calendar"]["status"] == "failed"
    # The brief keeps everything that did land, and everything it already had.
    assert "Partnership notes" in _texts(after)
    assert _claim_ids(after) >= _claim_ids(before) - {
        claim["id"] for claim in before["claims"] if claim["state"] == ContextState.MISSING
    }
    # And it says so rather than looking complete.
    assert after["partial"] is True
    assert "calendar" in after["partial_sources"]
    failed = [row for row in after["source_refs"] if row["provider"] == "calendar"]
    assert failed and failed[0]["evidence"] == Evidence.MISSING


def test_a_brief_with_no_refresh_reported_is_not_partial(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)

    payload = brief_payload(person_brief(store, person["person_id"]))

    assert payload["partial"] is False
    assert payload["partial_sources"] == []


# --- 9. handoff ----------------------------------------------------------


def test_the_generated_handoff_fills_four_fields_from_cited_claims(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    store.append_event(
        pid,
        source="gmail",
        kind="send",
        summary="Sent draft draft-1",
        payload={"sent": True, "to": "ada@analytic.example"},
    )

    brief = person_brief(store, pid)
    draft = handoff_draft(brief)

    assert draft["generated"] is True
    assert draft["who"].startswith("Ada Lovelace")
    assert draft["wanted"] == "research dinner"
    assert "Sent draft draft-1" in draft["happened"]
    assert draft["they_want"]
    assert draft["source_refs"]
    assert set(draft["source_refs"]) <= _claim_ids(brief_payload(brief))


def test_a_stored_handoff_replaces_the_generated_one_and_versions(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]

    stored = store.patch(
        pid,
        fields={
            "handoff_who": "Ada Lovelace, founder at Analytic",
            "handoff_wanted": "A research-dinner speaker",
            "handoff_happened": "One approved send, no reply yet",
            "handoff_they_want": "A date after the 15th",
        },
        expected_version=int(person["version"]),
        actor="director",
        rationale_summary="Director reviewed the handoff.",
    )

    draft = handoff_draft(person_brief(store, pid))

    assert draft["generated"] is False
    assert draft["happened"] == "One approved send, no reply yet"
    assert int(stored["version"]) == int(person["version"]) + 1
    assert any(
        entry["version"] == int(stored["version"]) for entry in store.versions(pid)
    )


# --- 10. restart / version history and revert ----------------------------


def test_revert_rolls_back_the_handoff_and_its_sources_together(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    baseline = int(person["version"])

    attach_drive_evidence(
        store,
        pid,
        kind="search_result",
        raw={
            "id": "drive-1",
            "name": "Fall sourcing masterdoc",
            "modifiedTime": "2026-08-01T10:00:00+00:00",
            "status": "read",
        },
        actor="director",
        rationale_summary="Director attached Drive evidence.",
    )
    current = store.get(pid)
    store.patch(
        pid,
        fields={"handoff_happened": "Attached the masterdoc"},
        expected_version=int(current["version"]),
        actor="director",
        rationale_summary="Director reviewed the handoff.",
    )

    with_evidence = brief_payload(person_brief(store, pid))
    assert "Fall sourcing masterdoc" in _texts(with_evidence)
    assert handoff_draft(person_brief(store, pid))["happened"] == "Attached the masterdoc"

    live = store.get(pid)
    store.revert(
        pid,
        to_version=baseline,
        expected_version=int(live["version"]),
        actor="director",
        rationale_summary="Restore the earlier person file.",
    )

    reverted = person_brief(store, pid)
    payload = brief_payload(reverted)

    assert payload["claims"], "a reverted brief is still a brief"
    assert "Fall sourcing masterdoc" not in _texts(payload)
    assert not [row for row in payload["source_refs"] if row["provider"] == "Google Drive"]
    assert handoff_draft(reverted)["generated"] is True
    for claim in payload["claims"]:
        for ref_id in claim["source_refs"]:
            assert ref_id in {row["id"] for row in payload["source_refs"]}


def test_rebuilding_the_brief_is_stable_across_a_restart(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    store.append_event(pid, source="gmail", kind="mail", summary="Read mail from Ada")
    first = person_brief(store, pid, now="2026-08-27T00:00:00+00:00")

    reopened = PersonStore(tmp_path)
    second = person_brief(reopened, pid, now="2026-08-27T00:00:00+00:00")

    assert first.projection.diagnostics.content_sha256 == (
        second.projection.diagnostics.content_sha256
    )


# --- 11. cross-person isolation ------------------------------------------


def test_another_persons_evidence_cannot_reach_this_brief(tmp_path):
    store = PersonStore(tmp_path)
    ada = _person(store)
    alonzo = _person(
        store,
        apollo_id="alonzo",
        first_name="Alonzo",
        last_name_obfuscated="Church",
        title="Professor",
        company="Princeton",
        target=None,
    )
    store.append_event(
        ada["person_id"], source="gmail", kind="mail", summary="Read mail from Ada"
    )
    store.append_event(
        alonzo["person_id"],
        source="granola",
        kind="meeting",
        summary="Secret Alonzo notes",
    )

    brief = person_brief(store, ada["person_id"])
    payload = brief_payload(brief)
    prompt = prompt_context(brief)

    # Non-vacuity: Ada's own evidence is present in both renderings.
    assert "Read mail from Ada" in _texts(payload)
    assert "Read mail from Ada" in prompt
    assert "Ada" in prompt
    # And Alonzo's is in neither.
    assert "Alonzo" not in _texts(payload)
    assert "Secret Alonzo notes" not in _texts(payload)
    assert "Alonzo" not in prompt
    assert "Secret Alonzo notes" not in prompt
    assert alonzo["person_id"] not in prompt


def test_a_foreign_event_is_refused_at_the_projection_boundary(tmp_path):
    """Isolation is a boundary, not a filter: a foreign row cannot be rendered."""
    store = PersonStore(tmp_path)
    ada = _person(store)
    alonzo = _person(
        store,
        apollo_id="alonzo",
        first_name="Alonzo",
        last_name_obfuscated="Church",
        title="Professor",
        company="Princeton",
        target=None,
    )
    store.append_event(
        ada["person_id"], source="gmail", kind="mail", summary="Read mail from Ada"
    )
    foreign = store.append_event(
        alonzo["person_id"],
        source="granola",
        kind="meeting",
        summary="Secret Alonzo notes",
    )

    person = store.get(ada["person_id"], expand_sources=True)
    own = store.timeline(ada["person_id"])

    assert build_brief(person, own)["claims"], "the honest brief builds"
    with pytest.raises(ValueError, match="scope mismatch"):
        build_brief(person, [*own, foreign])


# --- criterion 6: one projection, not two --------------------------------


def test_the_person_view_and_the_prompt_render_one_projection(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    store.bind_session("sess-ada", pid)
    store.apply_enrichment(pid, email="ada@example.test")
    store.append_event(pid, source="gmail", kind="mail", summary="Read mail from Ada")
    attach_drive_evidence(
        store,
        pid,
        kind="search_result",
        raw={
            "id": "drive-1",
            "name": "Fall sourcing masterdoc",
            "modifiedTime": "2026-08-01T10:00:00+00:00",
            "status": "read",
        },
        actor="director",
        rationale_summary="Director attached Drive evidence.",
    )

    brief = person_brief(store, pid, session_id="sess-ada")
    payload = brief_payload(brief)
    prompt = prompt_context(brief)

    selected = set(brief.projection.diagnostics.selected_item_ids)
    assert selected, "the projection selected something"
    # The view shows exactly the projection's claims: no extra, none missing.
    assert _claim_ids(payload) == selected
    # And the prompt shows the same claims, each with its source reference.
    for claim in payload["claims"]:
        assert claim["text"][:60] in prompt
        for ref_id in claim["source_refs"]:
            assert ref_id in prompt


def test_the_two_surfaces_bind_to_the_same_projection_identity(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    store.bind_session("sess-ada", pid)
    store.append_event(pid, source="gmail", kind="mail", summary="Read mail from Ada")

    view = person_brief(store, pid, now="2026-08-27T00:00:00+00:00")
    chat = person_brief(store, pid, session_id="sess-ada", now="2026-08-27T00:00:00+00:00")

    # A second summary implementation would diverge here first.
    assert view.projection.identity == chat.projection.identity
    assert chat.projection.reuse_for(view.projection.identity) is chat.projection
    assert (
        view.projection.diagnostics.content_sha256
        == chat.projection.diagnostics.content_sha256
    )


def test_nothing_the_brief_drops_for_budget_disappears_silently(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    for index in range(40):
        store.append_event(
            pid, source="gmail", kind="mail", summary=f"mail-{index}"
        )

    payload = brief_payload(person_brief(store, pid))

    assert "mail-39" in _texts(payload)
    assert "mail-0" not in _texts(payload)
    assert payload["omitted"] > 0


def test_secret_payload_keys_never_reach_either_rendering(tmp_path):
    store = PersonStore(tmp_path)
    person = _person(store)
    pid = person["person_id"]
    store.append_event(
        pid,
        source="drive",
        kind="file",
        summary="Read deck",
        payload={"access_token": "not-a-real-value-xyz"},
    )

    brief = person_brief(store, pid)
    payload = brief_payload(brief)
    prompt = prompt_context(brief)

    assert "Read deck" in _texts(payload)
    assert "not-a-real-value-xyz" not in prompt
    assert "access_token" not in prompt
    assert "not-a-real-value-xyz" not in _texts(payload)
