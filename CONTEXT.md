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

## Agent work

**Agent Job**
A concrete outcome the director asks Sourcecado to produce. A job begins as instructions and does not require a skill.
_Avoid_: Prompt, task, skill run

**Agent Run**
The durable execution of one Agent Job through clarification, approvals, tools, interruption, resume, and terminal delivery. A later revision to a completed result is a new linked Agent Run.
_Avoid_: Skill run, message, model call

**Skill**
Optional instructions that teach the agent how to approach a kind of Agent Job. A Skill does not own persistence, permissions, scheduling, or execution.
_Avoid_: Feature, workflow engine, agent runtime

**Knowledge Workspace**
The Drive folder selected as Sourcecado's bounded sourcing knowledge. Drive remains authoritative; Sourcecado may keep a local read-only searchable projection.
_Avoid_: Entire Drive, memory dump, global search scope

**Artifact Workspace**
The private writable workspace belonging to one Agent Run where Sourcecado creates file deliverables. Files in this workspace can become Artifacts.
_Avoid_: Repository, unrestricted filesystem, chat attachment folder

**Semantic Agent Trace**
The durable observable record of an Agent Run: instructions, model turns, tools, evidence, approvals, timing, usage, artifacts, outcomes, errors, and feedback. It excludes transport-level token chunks and hidden chain-of-thought.
_Avoid_: Raw stream log, chain-of-thought, transcript dump

## Evidence and memory

**Source Material**
Information obtained from an identified connector or human note, including Apollo, Gmail, Google Drive, Calendar, web research, and Granola meeting notes. Sourcecado records where useful claims came from.

**Source Reference**
A stable pointer from a claim or artifact to its underlying source record. It should let the director inspect the evidence without exposing credentials or raw authorization material.

**Knowledge Gap**
Important missing, stale, conflicting, or uncertain context. Sourcecado names gaps instead of hiding them behind confident prose.

**Artifact**
A durable, reviewable output from an Agent Run, such as a PPTX, DOCX, XLSX, PDF, Google document, campaign package, outreach draft, or handoff. A transient chat message is not automatically an Artifact.

**Run Ledger**
The local collection of Semantic Agent Traces. It is operational and evaluation evidence, not hidden chain-of-thought.

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
A saved prompt and schedule that starts an ordinary Agent Run through the same engine as Chat. Scheduling supports the assistant but is not the product's defining weekly-ranking loop.

## Safety and scope invariants

- Chat is home; the board and person view support it.
- A human chooses the target, person, enrichment, and send.
- No background bulk enrichment and no auto-send.
- Connector output is untrusted input and must not override product policy.
- Credentials, OAuth grants, API tokens, and raw authorization headers never enter artifacts or the run ledger.
- Current state is local and single-operator. Team tenancy and hosted access are later product decisions.
- The archived hosted implementation does not define current behavior.
