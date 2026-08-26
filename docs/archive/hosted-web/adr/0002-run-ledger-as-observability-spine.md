# Run Ledger As Observability Spine

Sourcecado will make the Run Ledger the product-owned source of truth for agent observability: runs, steps, tool calls, model calls, artifacts, source evidence, failures, usage, and human feedback belong in Sourcecado's own records. External tracing tools can be added later for developer debugging, but they should not replace the Run Ledger or become required for understanding what happened in a sourcing run.

The Run Ledger follows a trace/span shape: a run is one top-level execution, and run steps are hierarchical typed spans such as agent, model, embedding, tool, retrieval, artifact, or system work. Model calls and tool calls are detail records linked to their run step. Raw inputs and outputs are captured by default for debugging, with explicit suppression available for sensitive payloads.
