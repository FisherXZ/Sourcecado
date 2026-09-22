"""Opt-in live provider smoke; excluded from normal deterministic verification."""

import asyncio
import json
import os

import pytest

from coworker.provider import StreamKind, provider_from_env


@pytest.mark.skipif(
    os.environ.get("SOURCECADO_LIVE_PROVIDER_SMOKE") != "1",
    reason="set SOURCECADO_LIVE_PROVIDER_SMOKE=1 to call the configured provider",
)
def test_configured_provider_live_smoke_records_reproducibility_metadata():
    provider = provider_from_env()
    assert provider is not None, "configure one verified provider before live smoke"

    async def consume():
        return [
            event
            async for event in provider.astream(
                context_id="live-provider-smoke",
                messages=[
                    {
                        "role": "user",
                        "content": "Reply exactly SOURCECADO_PROVIDER_SMOKE_OK.",
                    }
                ],
            )
        ]

    events = asyncio.run(consume())
    record = {
        "provider": provider.provider_id,
        "model": provider.model_id,
        "prompt_version": "provider-conformance-v1",
        "nondeterministic": True,
    }

    print(json.dumps(record, sort_keys=True))
    assert events[0].kind is StreamKind.START
    assert any(event.kind is StreamKind.TERMINAL for event in events)
