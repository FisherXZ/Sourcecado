from coworker.people import PersonStore
from coworker.server import system_prompt
from coworker.store import ConversationStore


def test_bound_prompt_includes_ada_not_alonzo(tmp_path):
    conv = ConversationStore(tmp_path)
    people = PersonStore(tmp_path)
    ada = people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
        target="research dinner",
    )
    alonzo = people.keep_from_apollo(
        apollo_id="alonzo",
        first_name="Alonzo",
        last_name_obfuscated="C",
        title="Professor",
        company="Princeton",
    )
    people.bind_session("sess-ada", ada["person_id"])
    people.append_event(
        ada["person_id"],
        source="gmail",
        kind="mail",
        summary="Read mail from Ada",
    )
    people.append_event(
        alonzo["person_id"],
        source="granola",
        kind="meeting",
        summary="Secret Alonzo notes",
    )
    prompt = system_prompt(conv, people=people, session_id="sess-ada")
    assert "Person file:" in prompt
    assert "Ada" in prompt
    assert "Read mail from Ada" in prompt
    assert "Alonzo" not in prompt
    assert "Secret Alonzo notes" not in prompt


def test_unbound_prompt_has_no_person_file(tmp_path):
    conv = ConversationStore(tmp_path)
    people = PersonStore(tmp_path)
    people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    prompt = system_prompt(conv, people=people, session_id="sess-other")
    assert "Person file:" not in prompt


def test_long_timeline_keeps_newest_summaries(tmp_path):
    conv = ConversationStore(tmp_path)
    people = PersonStore(tmp_path)
    ada = people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    people.bind_session("sess-ada", ada["person_id"])
    for i in range(20):
        people.append_event(
            ada["person_id"],
            source="gmail",
            kind="mail",
            summary=f"mail-{i}",
        )
    prompt = system_prompt(conv, people=people, session_id="sess-ada")
    assert "mail-19" in prompt
    assert "mail-0" not in prompt


def test_prompt_does_not_include_access_token(tmp_path):
    conv = ConversationStore(tmp_path)
    people = PersonStore(tmp_path)
    ada = people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
    )
    people.bind_session("sess-ada", ada["person_id"])
    people.append_event(
        ada["person_id"],
        source="drive",
        kind="file",
        summary="Read deck",
        payload={"access_token": "ya29.secret"},
    )
    prompt = system_prompt(conv, people=people, session_id="sess-ada")
    assert "ya29.secret" not in prompt
    assert "access_token" not in prompt
