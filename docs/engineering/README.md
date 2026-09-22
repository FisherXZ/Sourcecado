# Engineering guides

Setup and contribution instructions live in [CONTRIBUTING.md](../../CONTRIBUTING.md).
These guides explain how the running application works and the constraints to
preserve when changing it.

```text
React UI (frontend/src/)
    | local authenticated HTTP and WebSocket
FastAPI (backend/coworker/)
    | agent loop, permissions and connectors
Person files, conversations, schedules and run ledger

Tauri (frontend/src-tauri/) starts and stops the backend.
```

The browser development server proxies `/v1` and `/ws` to the backend. The native
shell starts its own backend, injects its address and token, and hides to the menu
bar when the window closes. Quit stops the application. Development uses the
Python venv; a packaged app uses the frozen backend.

## Runtime and state

- [Runtime language](runtime-language.md)
- [Agent runs and recovery](agent-runs.md)
- [Tool availability and permissions](effective-tools.md)
- [Run ledger](run-ledger.md), [budgets](run-budgets.md), and [evaluation harness](evaluations.md)
- [Compaction](compaction.md), [context projection](context-projection.md), and [evidence envelope](evidence-envelope.md)

## Sourcing

- [Reviewed enrichment and approved sending](approved-send.md)
- [Reply filing](reply-filing.md)
- [Living brief](living-brief.md)
- [Meeting evidence](meeting-evidence.md) and [legal artifacts](legal-artifacts.md)

## Operating and packaging

- [Doctor and migrations](doctor.md)
- [Secret scan](secret-scan.md) and [diagnostic bundles](diagnostic-bundle.md)
- [macOS packaging](packaging.md)
- [Preview updates and rollback](update-channel.md)

## Decisions

- [Manual runs preserve the weekly slot](adr/0001-manual-run-does-not-consume-weekly-slot.md)
- [Workspace authority and shell execution](adr/0002-sourcecado-workspace-runtime.md)
- [macOS preview packaging](adr/0003-macos-preview-artifact-packaging.md)

Product language in [domain.md](../domain.md) takes precedence over runtime and
course terminology. The [product specification](../superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md)
defines the job these systems serve.
