# Sourcecado as the sourcing director's assistant

Date: 2026-08-25
Status: Current product source of truth (adopted 2026-08-26)
Spring: the active local product pass after the operator window
Supersedes as product shape and implementation scope: the "Monday packet / ranked shortlist" framing and the hosted-team build. The 2026-06-08 OS design remains prior art for identity, citations, and durable memory; it is not binding current scope. This document is what Sourcecado is *for*.

**How to read this.** The product is the sourcing director's job, done with an assistant. Use cases are the source of truth. Surfaces exist because those use cases need a place to live. If you are implementing, start from the job and the scenes. Do not start from the locked conditions at the bottom. Those are constraints on the use cases, not a backlog.

---

## The job

A Codeology sourcing director sends a lot of email. The work is outbound: find the right people, write something that is actually for them, send it, then be a human in the relationship.

The email has to be good. Not a blast. Title, company, and why we are writing are enough to start. After it is sent, the job is human: the meeting, the follow-up conversation, the project. The assistant's job around that is context, prep, and memory. If we land a project with a team, the next officer should be able to open that person and see what happened, what they want, and where it stands.

That is the whole product. Everything else is how we make that job easier.

---

## The product

Sourcecado is an executive assistant that reports to the sourcing director.

The director is the principal. Sourcecado does the gathering, the drafting, the tracking, and the filing. The director decides who is worth writing, what gets sent, and what happens in the room.

They talk to the assistant in chat. That is home. They check the assistant's operating picture on a dashboard in the rail. They open a person when they need the file.

Sourcecado is not a generic coworker that happens to have Gmail. It is not a weekly ranking engine. It is not HubSpot. It is the assistant for this one job.

This spring it is Fisher's assistant on his machine. The file is written so another officer could read it later. Shared login is not this spring.

---

## Use cases

These are the work. If a change does not make one of these easier, it is not this product.

### 1. Start outreach for a target

The director has a reason to write people: a theme, a kind of company, a role, a club need. They tell the assistant. The assistant searches Apollo and comes back with people to consider, each with the context it already has. The director curates. Sourcecado does not invent the target. The director does not have to live in Apollo.

### 2. Send a lot of tailored email

The volume is real. The quality bar is "this was written for you," not "this was generated." For a new person, Apollo fields plus the target are enough to draft. The director reviews the draft in Sourcecado and sends from Sourcecado. Nothing goes out without that click.

Getting a real email address spends Apollo credits. The director does that on purpose, one person at a time. Sourcecado does not enrich a list in the background.

### 3. Keep work in motion

Outreach is not one email and a prayer. A person the director keeps appears in the Board's Backlog before outreach starts. Each person we are actively working is a sequence. The assistant keeps the picture of what is waiting in Backlog, what is open, what is in conversation, and what is done, so the director is not reconstructing it from Gmail search.

Follow-ups, "what did we last say," and "is this still alive" are the assistant's job. The conversation itself stays human.

### 4. Walk into the room prepared

Context is not a research project we run before a meeting. It is the living brief that starts when we first draft that person and thickens as mail, notes, and files show up. Meeting prep is that brief, ready, not a separate deliverable the director has to remember to ask for.

### 5. Leave a file someone else can pick up

When the work is done, or when a project lands, the next person should not start from zero. The person file is the full thread of what we know: mail, Drive, meeting notes, and the rest, plus a short handoff of who they are, what we wanted, what happened, and what they want. That is how sourcing survives officer turnover.

---

## How a day feels

The director opens Sourcecado and is in the thread with the assistant.

They can say the target and get people back. They can say "write this one" and get a draft. They can say "what do I need for this conversation" and the brief is already on the person.

When they want the operating picture, they open the dashboard: who is in Backlog, who is queued to write, which sequences are in conversation, what is done. It is the EA putting the board on the table for the principal. It is not a CRM they have to live in.

When they need the record, they open the person. A small panel of the same brief sits beside whatever they are doing, so they do not have to leave the work to remember who this is.

They never send from a spreadsheet. They never lose the thread because it lived in someone's inbox.

---

## What stays human

Sending the first judgment: this person, this message, now.

The meeting.

The relationship after they reply.

Sourcecado can draft the follow-up and keep the file. Sourcecado does not become the person in the room.

---

## What this spring is for

Make the assistant real for that job, for Fisher, on Sourcecado.

At the end of the spring, a director can name a target, get people, draft from what Apollo already knows, enrich a person when they choose to, send from Sourcecado, see that person on the board as a sequence, and open a file that will still make sense when someone else reads it.

The operator window in flight stays the conversation home. The dashboard is how the assistant reports. Do not flip the product to "dashboard-first" and do not treat chat polish as the spring.

Later springs, not this one: other directors on the same brain, Sourcecado sending follow-ups on its own, Sourcecado enriching a queue on its own.

---

## What we are not building

A generic personal assistant.

A 25-app coworker.

A HubSpot clone (deals, amounts, forecasts, pipelines). HubSpot is only a reference for "there is a live piece of work, with a person, with a history."

A weekly autonomous ranking run as the product.

Auto-send. Auto-enrich.

Team tenancy and hosted shared login.

---

## Locked conditions

These are true. They are not the outline of the work. They exist so an implementation pass does not re-litigate the job.

- Chat is home. The operating board is a destination in the rail.
- A sequence is a person we are working. A company is how we tag that person, not a separate kind of work item. Do not call it a deal.
- Kept people appear in the Board's Backlog before they enter a sequence. Backlog is a Board lane, not a sequence state.
- Sequences move Open → In conversation → Done. Exactly three sequence states. The director or the assistant moves them.
- Intake is Apollo search from a target the director named.
- A draft may be queued from Apollo fields and the target. Cited web research is welcome in the brief. It is not a gate on drafting.
- Enrich is manual and director-driven.
- Send is approve-to-send in Sourcecado. That replaces the old drafts-only guardrail for this spring.
- The person file is the timeline of related Gmail, Drive, notes, and the rest, plus a handoff summary.
- The living brief exists from the first draft. Meeting prep is that brief.
- This spring is local, one operator.

The OS work that makes those use cases trustworthy (storing the people and sequences, attaching search and mail to the file, a durable record of what Sourcecado did, permission on enrich and send) is in service of the job above. It is not a separate product.
