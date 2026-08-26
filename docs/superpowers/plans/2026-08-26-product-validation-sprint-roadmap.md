# Sourcecado Product Validation Sprint Roadmap

Date: 2026-08-26  
Status: baseline executed; architecture basis approved
Product source: `docs/superpowers/specs/2026-08-25-sourcecado-sourcing-director-spring.md`

## Goal

Prove that Sourcecado's local agent operating system can complete two valuable
Sourcing Director jobs: an outreach campaign and a company pitch package. The
skills are product probes, not isolated features. Real runs determine which
runtime, connector, memory, approval, artifact, and scheduling work enters the
roadmap.

## Golden outcomes

### Outreach campaign

A director can move from a natural-language campaign objective to a qualified,
reviewed, sent, and trackable set of personalized emails. The run preserves the
people, rationale, sources, approvals, messages, sequence state, and follow-up
work.

### Company pitch package

A director can move from a named company and objective to a source-grounded,
editable company deck plus a brief, talking points, knowledge gaps, and a
follow-up draft. The result uses the correct SOP and approved template.

## Execution order

### 0. Mature-agent-system blueprint

Compare OpenClaw, Grok Bot, and OpenWorker by subsystem. For every subsystem,
record what Sourcecado copies, adapts, already owns, or rejects. Lock the target
invariants for durable runs, events, tools, approvals, artifacts, memory,
skills, connectors, scheduling, recovery, and operator control.

### 1. Test contracts

Choose one real outreach objective and one real company. Record the inputs,
source scope, constraints, required artifacts, rejection conditions, and
manual acceptance rubric for each workflow.

### 2. Minimal v0 skills and readiness

Write the smallest executable `outreach-campaign` and
`company-pitch-package` skills. Verify the model, Apollo, Gmail, Drive,
Calendar, Granola, web, skill loader, approval inbox, local persistence, and
scheduler without hiding missing capabilities behind skill prose.

### 3. Baseline product runs

Drive both workflows through the local app. Exercise multi-turn clarification,
real tools, review and approval, interruption, restart, resume, artifacts,
memory, Board state, and a scheduled invocation where appropriate. Preserve
the final deliverables and every manual intervention.

### 4. Evidence-based gap map

Classify each failure as skill logic, tool/connector, durable runtime,
approval, artifact, memory/provenance, scheduling, UI, domain model, or output
quality. Every gap needs a reproduction, product impact, relevant reference
pattern, priority, and dependency.

### 5. Roadmap reconciliation and report

Compare the observed gaps with current code, open issues, and the reference
blueprint. Retain work that unlocks the two outcomes, re-scope speculative
platform work, and report the evidence and recommendations to Fisher.

### 6. Final plan and implementation

After the decision gate, implement approved gaps as vertical slices. Prefer
copying a proven reference pattern over inventing a local mechanism. Re-run the
affected workflow after every meaningful slice. Interactive and scheduled
execution must use the same skill and run path.

### 7. Acceptance rerun

The sprint is complete only when Fisher accepts both deliverables; both runs
survive interruption and restart; at least one executes through scheduling;
all sources, approvals, artifacts, failures, and outcomes are durable; and a
later conversation can use the resulting memory without repeating the work.

## Guardrails

- The agent operating system is the product; the skills expose its maturity.
- Do not add generic platform breadth without evidence from a golden workflow.
- Do not claim an external action or artifact exists unless a tool receipt proves it.
- Costly and external actions remain explicit and approval-gated.
- Use real Codeology sources, but never place secrets in prompts, logs, or plans.
- Treat unmerged issue branches as experiments until they land and pass the
  product run.

## Evidence produced by this sprint

- Mature-agent-system reference blueprint.
- Two scenario contracts and two v0 skills.
- Connector/readiness report.
- Full run transcripts, receipts, artifacts, and acceptance notes.
- Layered gap map with reference implementations.
- Reconciled implementation roadmap.
- Final accepted outreach campaign and company pitch package.

## Current execution status

- Reference blueprint: complete.
- Scenario contracts: complete.
- V0 skills: complete and live-loaded.
- Connector preflight: complete.
- Interactive baselines: complete through the director-review gate.
- Interruption and reload recovery: verified.
- Gap map and evidence reconciliation: complete.
- Core Agent OS architecture interview: complete and approved.
- Grounded implementation roadmap: complete.
- External writes and scheduled rerun: waiting on the implementation slices
  and exact director approvals.

See `docs/qa/2026-08-26-golden-workflow-baseline.md` and
`docs/superpowers/plans/2026-08-26-evidence-reconciled-agent-os-roadmap.md`.
