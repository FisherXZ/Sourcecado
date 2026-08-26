from coworker.brief import build_brief
from coworker.people import PersonStore


def test_apollo_only_brief_names_who_and_missing(tmp_path):
    store = PersonStore(tmp_path)
    person = store.keep_from_apollo(
        apollo_id="abc123",
        first_name="Alyssa",
        last_name_obfuscated="W***n",
        title="Partner",
        company="Codeology",
        target="club research dinner",
    )
    brief = build_brief(person, [])
    assert "Alyssa" in brief["who"]
    assert "Partner" in brief["who"]
    assert "Codeology" in brief["who"]
    assert brief["why"] == "club research dinner"
    assert brief["sources"] == []
    missing = " ".join(brief["missing"]).lower()
    assert "email" in missing or "mail" in missing


def test_brief_keeps_all_sources_when_calendar_is_absent(tmp_path):
    store = PersonStore(tmp_path)
    person = store.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
        target="research dinner",
    )
    pid = person["person_id"]
    store.append_event(pid, source="apollo", kind="enrich", summary="Enriched Ada", payload={"email": "ada@analytic.example"})
    store.append_event(pid, source="gmail", kind="mail", summary="Read mail from Ada")
    store.append_event(pid, source="drive", kind="file", summary="Read deck")
    store.append_event(pid, source="granola", kind="meeting", summary="Meeting notes")
    brief = build_brief(store.get(pid), store.timeline(pid))
    assert brief["sources"] == ["apollo", "gmail", "drive", "granola"]
    assert "calendar" not in brief["sources"]
    assert brief["learned"]
    assert "Enriched Ada" in brief["learned"]
    assert "Read deck" in brief["learned"]
