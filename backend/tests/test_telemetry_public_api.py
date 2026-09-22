def test_telemetry_package_exposes_the_integration_boundary():
    from coworker.telemetry import (
        AgentTurnSpan,
        CostEstimate,
        InMemoryTelemetryAdapter,
        NoOpTelemetryAdapter,
        ProviderSpan,
        TelemetryRecorder,
        ToolSpan,
        TraceContext,
        UsageEvent,
    )

    disabled = TelemetryRecorder(NoOpTelemetryAdapter())
    local = TelemetryRecorder(InMemoryTelemetryAdapter())

    assert disabled.enabled is False
    assert local.enabled is True
    assert all(
        value is not None
        for value in (
            AgentTurnSpan,
            CostEstimate,
            ProviderSpan,
            ToolSpan,
            TraceContext,
            UsageEvent,
        )
    )
