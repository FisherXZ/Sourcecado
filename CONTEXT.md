# SourcyAvo

SourcyAvo is an internal Codeology sourcing context. It defines the language around sourcing work, shared outreach memory, and the people who use that memory.

## Language

**Sourcing**:
The work of identifying promising external contacts, understanding why they matter to Codeology, and managing outreach to them. Sourcing is external-facing relationship work, not general member or applicant recruiting.
_Avoid_: Recruiting, admissions, applicant outreach

**Contact**:
A person or organization that Codeology may want to build an external relationship with. Outreach history, notes, tags, and recommendations are understood in relation to Contacts.
_Avoid_: Lead, prospect, account

**Sourcing Lead**:
A Contact currently worth considering for outreach because there is a timely reason Codeology may want to build or renew the relationship. A Sourcing Lead is a state of a Contact, not a separate kind of record.
_Avoid_: Prospect, target, recommended Contact

**Sourcing Memory System**:
The sourcing-specific memory layer that helps Codeology remember relationships, past decisions, internal context, sourcing outcomes, and useful patterns across semesters. SourcyAvo is the sourcing-facing product experience built on this memory system and connected to a capable agent harness.
_Avoid_: Chatbot, document search, generic RAG

**Sourcing Memory Answer**:
A synthesized answer from the Sourcing Memory System that directly responds to a sourcing question, cites where its claims came from, and names important missing context.
_Avoid_: Search result, raw retrieval, generated response

**Research Chat**:
The first user-facing SourcyAvo interface where a Sourcing Director asks sourcing questions and receives Sourcing Memory Answers. Dedicated dashboards, browsing panels, and management pages are deferred until after the MVP.
_Avoid_: Dashboard, admin panel, CRM

**Agent Harness**:
The runtime that connects SourcyAvo's Sourcing Memory System to a capable agent experience. The memory system should be portable enough to work with OpenClaw-, Hermes-, Codex-, or Claude-style agent harnesses rather than being trapped inside one chat UI.
_Avoid_: Chat wrapper, app shell, model

**Source Citation**:
A reference to the specific source record behind a claim in a Sourcing Memory Answer. Source Citations help Sourcing Directors verify where SourcyAvo's understanding came from.
_Avoid_: Link, footnote, evidence blob

**Knowledge Gap**:
Important missing, stale, or uncertain context that SourcyAvo should call out instead of hiding. Knowledge Gaps tell Sourcing Directors what still needs manual checking or follow-up.
_Avoid_: Error, hallucination, unknown

**Relationship Graph**:
The sourcing-specific map of how Contacts relate to people, companies, events, semesters, outreach efforts, and outcomes. It helps Sourcing Directors understand relationships, not just isolated notes.
_Avoid_: Social graph, database schema, network

**Memory Refresh**:
The recurring or manually triggered work of updating the Sourcing Memory System with newly available sourcing context. Memory Refresh keeps institutional knowledge from going stale across semesters.
_Avoid_: Dream cycle, sync job, ingestion daemon

**Source Material**:
The trusted records SourcyAvo can use to build the Sourcing Memory System. The first Source Materials are Google Drive docs, Google Sheets, Notion workspaces, and exported email outreach threads; Messenger may become Source Material if access and privacy boundaries are resolved.
_Avoid_: All data, every file, random context

**Restricted Material**:
Source Material that should only be visible to specific Codeology roles because it contains sensitive, private, officer-only, or access-controlled information. Restricted Material must not appear in normal Sourcing Memory Answers for people who are not allowed to see it.
_Avoid_: Secret data, private stuff, hidden context

**Officer Access**:
The initial MVP access boundary for SourcyAvo. Officer Access means the Sourcing Memory System is available to Codeology officers, with narrower access rules added for especially restricted material.
_Avoid_: Public access, all-member access, open access

**Public Club Context**:
Source Material safe to expose broadly, such as public Codeology information, public project summaries, and website content.
_Avoid_: Public memory, open data

**Officer Context**:
Source Material intended for Codeology officers, such as officer workspaces, semester planning, sourcing tasks, and internal documentation.
_Avoid_: Internal data, admin context

**Restricted Sourcing Context**:
Source Material that needs the narrowest MVP access boundary, such as outreach threads, sensitive Contact notes, alumni or leadership Messenger context, and private follow-up state.
_Avoid_: Secret sourcing data, private CRM

**Notion Workspace**:
Codeology's semester operating hub, including officer workspaces, sourcing pages, task tables, resource links, and documentation. Notion Workspace content is primary Source Material for semester context and officer work, but not a replacement for Drive, Sheets, or outreach records.
_Avoid_: Public docs, generic notes, website content

**Semester Context**:
The current and historical operating context for a Codeology semester, including leadership roles, officer workspaces, sourcing tasks, project activity, events, and relevant resources.
_Avoid_: Timeline, schedule, calendar

**Past Collaborator**:
A Contact that Codeology has already worked with in some concrete way, such as speaking, sponsoring, mentoring, partnering, or supporting a past club effort.
_Avoid_: Partner, connection, worked-with person

**Outreach History**:
The record of who Codeology contacted, why they were contacted, what was sent, who responded, who needed follow-up, and what ultimately happened. Outreach History includes both successes and misses so Sourcing Directors can learn what worked and what did not.
_Avoid_: Message log, email archive, CRM data

**Outreach Outcome**:
The result of a sourcing attempt with a Contact, such as responded, did not respond, needs follow-up, worth researching more, worked with Codeology, or not a fit.
_Avoid_: Status, result, disposition

**Sourcing Director**:
A Codeology officer responsible for finding, prioritizing, and contacting potential people, organizations, alumni, sponsors, speakers, or partners for club-related outreach.
_Avoid_: General user, officer, member

## Example Dialogue

**Developer**: Who should the first version help?

**Domain Expert**: Start with Sourcing Directors. They are the people reconstructing outreach history and deciding who to contact next.

**Developer**: Does sourcing include applicant recruitment?

**Domain Expert**: No. In SourcyAvo, sourcing means external-facing relationship work for Codeology.

**Developer**: What is the main thing SourcyAvo manages?

**Domain Expert**: Contacts. A Contact can be either a person or an organization.

**Developer**: Is every Contact something we should reach out to?

**Domain Expert**: No. A Sourcing Lead is a Contact that is currently worth considering for outreach.

**Developer**: What does shared memory mean here?

**Domain Expert**: Think company brain, but for the club. SourcyAvo starts with Codeology's sourcing memory system.

**Developer**: What should that sourcing memory system remember first?

**Domain Expert**: It should remember people Codeology has worked with, what happened with each Contact, past semester sourcing efforts, cold outreach, responses, follow-ups, companies that responded, what worked, and what did not.

**Developer**: Should SourcyAvo copy the full GBrain system?

**Domain Expert**: No. SourcyAvo should teach the same memory-layer shape through smaller MVP pillars: synthesized answers, source citations, a relationship graph, knowledge gaps, and memory refresh.

**Developer**: Is SourcyAvo just a chat product?

**Domain Expert**: No. SourcyAvo is the sourcing-facing product experience. Underneath it is a sourcing memory system that should be portable enough to connect to capable agent harnesses such as OpenClaw-, Hermes-, Codex-, or Claude-style agents.

**Developer**: What should SourcyAvo treat as source material first?

**Domain Expert**: Start with Google Drive docs, Google Sheets, and exported email outreach threads. Messenger is valuable because alumni and leadership chats live there, but only if access and privacy can be figured out.

**Developer**: Does Notion count as sourcing source material?

**Domain Expert**: Yes. Codeology's Notion workspace contains semester hubs, officer workspaces, sourcing pages, task tables, Airtables, and resources. It should be treated as permissioned source material because some pages are officer-only or sensitive.

**Developer**: Is Notion the source of truth?

**Domain Expert**: Notion is primary for semester structure, officer workspaces, sourcing tasks, links, and responsibilities. Drive, Sheets, email exports, Messenger, and project archives still hold important source material that Notion may only point to.

**Developer**: Who can use the first version of SourcyAvo?

**Domain Expert**: Start with Officer Access. The Sourcing Memory System is not public to all active members, and especially sensitive Restricted Material needs narrower rules.

**Developer**: What access tiers does the MVP need?

**Domain Expert**: Use three tiers: Public Club Context, Officer Context, and Restricted Sourcing Context. Avoid per-person permissions in the first teaching version unless they become necessary.

**Developer**: What should the first interface be?

**Domain Expert**: Start with Research Chat. Dashboards, browsing panels, and management pages can wait until after the MVP.
