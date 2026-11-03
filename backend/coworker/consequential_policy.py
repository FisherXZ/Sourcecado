"""Sourcecado-owned consequential action names and supported read-only adapters."""
ACTION_TOOLS = frozenset({'gmail_send', 'apollo_enrich_contact'})


def requires_once(name: str) -> bool:
    return name in ACTION_TOOLS or name == "monid_research"


def supported_mcp_read(name: str) -> bool:
    # Only Sourcecado-owned connector capabilities can declare a free read.
    # A vendor naming a paid lookup get_/query_ does not establish that policy.
    return name in {
        'mcp__granola__list_meetings',
        'mcp__eval__environment_probe',  # Credential-free evaluation harness.
        'mcp__echo__ping',  # Bounded in-process diagnostic adapter.
    }
