# Golden Sourcing Workflow Test Contracts

Date: 2026-08-26  
Status: scenario structure locked; live targets require Fisher's selection

## Shared test protocol

For each workflow, preserve the initial request, clarifying turns, tool calls,
approvals, artifacts, final answer, runtime receipt, restart/resume evidence,
manual interventions, and Fisher's accept/reject notes.

Score by inspection on five dimensions: outcome completeness, factual and
source correctness, usefulness to the director, editability/reviewability, and
durability after the run.

## Scenario A: outreach campaign

### Director request

Run a real Codeology outreach campaign for an objective and target audience
selected by Fisher. The initial validation target is a project-partner or
sponsor campaign with a deliberately bounded contact count.

### Required inputs

- Campaign objective and desired relationship.
- Target roles, organizations, or company profile.
- Contact count and deadline.
- Exclusions and do-not-contact constraints.
- Canonical outreach SOP/template location.
- Whether the test may enrich, create drafts, and send after review.

### Required result

- Deduplicated shortlist with why-now and source evidence.
- Prior-relationship and prior-outreach check.
- Deliberate enrichment record for chosen contacts only.
- Personalized drafts following the approved SOP.
- Human-reviewed sends and durable receipts when authorized.
- Person files and correct sequence states.
- Campaign summary, failures, unsent contacts, and next actions.

### Reject when

The list is generic, evidence is invented, the SOP is not used, enrichment or
send happens without approval, messages are not meaningfully personalized,
successful work disappears after restart, or the final state cannot support
follow-up.

## Scenario B: company pitch package

### Director request

Prepare Codeology to approach one real company selected by Fisher for a named
partnership, sponsorship, speaker, or project objective.

### Required inputs

- Company and concrete objective.
- Audience, meeting/deadline, and desired ask.
- Canonical pitch SOP and approved slide template location.
- Known relationship/contact context.
- Output location and acceptable slide format.

### Required result

- Source-grounded company and relationship brief.
- Verified SOP and approved template selection.
- Editable company-specific deck, or an explicit failed capability result if
  the current system cannot produce one.
- Clear ask, talking points, citations, and knowledge gaps.
- Follow-up email draft.
- Durable artifact/source references and updated company/person context.

### Reject when

The wrong template or named parties are used, unsupported facts appear, the
agent claims to have edited a deck without a write receipt, the output is not
editable, the ask is unclear, or the package cannot be recovered later.

## Live-selection gate

Before baseline execution Fisher selects:

1. One outreach objective, target audience, contact count, and send boundary.
2. One company, desired ask, deadline, SOP/template folder, and required slide
   output format.
