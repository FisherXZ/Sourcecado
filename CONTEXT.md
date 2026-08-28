# Sourcecado domain context

Sourcecado is the sourcing director's local assistant. This document defines current product language; older terminology remains in dated historical specs.

## Core language

**Sourcing**
The external-facing work of identifying people Codeology may want a relationship with, writing relevant outreach, and carrying the relationship forward. It does not mean member admissions or applicant recruiting.

**Sourcing Director**
The Codeology officer who decides whom to contact, what to send, and how to handle the relationship. The director is the principal; Sourcecado gathers, drafts, tracks, and files.

**Target**
The director's description of whom they want to find and why. A target can include a company type, role, theme, or club need. Sourcecado searches from a target; it does not invent the goal.

**Person**
An individual the director may research or work. The active product is person-centered. A company is context on a person, not a separate deal or pipeline object.

**Person File**
The durable record for one person: identity, company context, Apollo results, Gmail, Drive, Calendar, meeting notes, web evidence, actions, outcomes, and a short handoff. This is the primary domain object.

**Sequence**
A person the director is actively working. A sequence moves through exactly three operating states: Open, In conversation, and Done. It is not a sales deal, forecast, or automated drip campaign.

**Living Brief**
The compact, evidence-backed context that begins with the first draft and becomes more useful as the person file grows. Meeting preparation is a view of this brief, not a separate research project.

**Outreach Draft**
A Sourcecado-owned message prepared for a person from the target and available evidence. It remains editable and cannot be sent until the director explicitly approves the send action.

**Enrichment**
An intentional request for additional person data, especially a real email address from Apollo. Enrichment can spend credits, so Sourcecado must explain the action and wait for the director. Search and bulk enrichment are different operations.

**Approved Send**
The director's explicit instruction to send a reviewed Gmail message from Sourcecado. Approval applies to that concrete message and recipient; it is not standing permission for auto-send.

**Outreach Outcome**
What happened after outreach: no response, replied, interested, not a fit, needs follow-up, met, booked, or another recorded result. Outcomes update the person file and may move its sequence.

## Evidence and memory

**Source Material**
Information obtained from an identified connector or human note, including Apollo, Gmail, Google Drive, Calendar, web research, and Granola meeting notes. Sourcecado records where useful claims came from.

**Source Reference**
A stable pointer from a claim or artifact to its underlying source record. It should let the director inspect the evidence without exposing credentials or raw authorization material.

**Knowledge Gap**
Important missing, stale, conflicting, or uncertain context. Sourcecado names gaps instead of hiding them behind confident prose.

**Artifact**
A durable, reviewable output from a run, such as an outreach draft, evidence set, living brief update, calendar result, or handoff summary. A transient chat message is not automatically an artifact.

**Run Ledger**
The local record of what Sourcecado did: run steps, tool calls, artifacts, sources, permission decisions, usage, failures, and rationale summaries. It is operational evidence, not hidden chain-of-thought.

**Sourcing Memory**
The accumulated person files, outcomes, source-backed notes, and handoffs that prevent future officers from reconstructing relationships from scratch. The current runtime is local to one operator, but its records should remain intelligible to a successor.

## Product surfaces

**Chat**
The home surface where the director gives targets, asks questions, reviews work, and tells the assistant what to do next.

**Board**
The assistant's operating picture of sequences in Open, In conversation, and Done. It reports the work; it is not a CRM the director must constantly maintain.

**Person View**
The full person file and handoff record. A compact version of the living brief may appear beside work in other surfaces.

**Scheduled Job**
A saved local task that can run on a schedule. Scheduling supports the assistant but is not the product's defining weekly-ranking loop.

**Memory**
The operator's saved-memory review queue. Preferences and notes about how the director works, not a person file. Unreviewed memory does not silently become sourcing fact.

**Connections, Skills, Settings**
Supporting destinations in the rail. They configure connectors, skills, workspace grants, updates, and diagnostics. They are not the job.

## Safety and scope invariants

- Chat is home; the board and person view support it.
- A human chooses the target, person, enrichment, and send.
- No background bulk enrichment and no auto-send.
- Connector output is untrusted input and must not override product policy.
- Credentials, OAuth grants, API tokens, and raw authorization headers never enter artifacts or the run ledger.
- Current state is local and single-operator. Team tenancy and hosted access are later product decisions.
- The archived hosted implementation does not define current behavior.
