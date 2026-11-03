"""A prepared Monid quote for the generic turn-loop gate tests. Fake transport only."""
from coworker.monid import MonidResearchActions
from coworker.secrets import SecretStore
from test_monid import KEY, MonidHttp


def prepared_monid(root, *, people, store, inbox, sid, agent_runs=None):
    """Return Monid actions and the arguments of one real, unexpired quote."""
    pid = people.person_for_session(sid)
    if pid is None:
        pid = people.keep_from_apollo(apollo_id="monid-fixture", first_name="Ada",
            last_name_obfuscated="Lovelace", title=None, company=None)["person_id"]
        people.bind_session(sid, pid)
    actions = MonidResearchActions(people=people, store=store, inbox=inbox,
        secrets_store=SecretStore(root / "monid-fixture-secrets.json"), http=MonidHttp(), agent_runs=agent_runs)
    actions.save_key(KEY)
    actions.check()
    quote = actions.prepare(pid, sid, "exa", "search", {"body": {"query": "Ada research", "numResults": 1}}, "0.02")
    return actions, {"quote_id": quote["quote_id"]}
