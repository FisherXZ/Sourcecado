# Sourcecado Course

The Sourcecado Course is a learning context around the Sourcecado product, not a second definition of the product. Students change the live repository.

## Learning work

**Course Repo**:
The live Sourcecado repository in which students create branches and propose changes through pull requests.
_Avoid_: Training repo, toy repo, disposable repo

**Guided Ticket**:
A real, instructor-curated product change with a specific learning purpose, explicit acceptance criteria, and stated non-goals. Guided Tickets have no artificial file-count, line-count, or duration limit.
_Avoid_: Exercise, tutorial task, homework problem

**Ticket Blueprint**:
The shared structure used to author Guided Tickets: user problem, learning objective, current behavior, acceptance criteria, non-goals, verification commands, live-demo requirement, and pull-request checklist.
_Avoid_: Step-by-step solution, ticket backlog, implementation recipe

**Build Project**:
A student-owned, multi-week product change that ends in a reviewed implementation, demonstration, and handoff.
_Avoid_: Final exercise, capstone app, greenfield project

**Teaching Week**:
One of the first five course weeks, combining a shared engineering concept with active work on Guided Tickets or Build Projects.
_Avoid_: Lecture-only week, theory week, pre-build week

**Open Build Week**:
One of the final four course weeks, reserved for Build Project implementation, review, integration, and recovery without a new lesson deck.
_Avoid_: Free week, empty week, catch-up week

**Eval Checkpoint**:
The mandatory Week 6 Build Project review in which students exercise an explicit behavior scenario, inspect the result, and decide what the project must change next without adding a new lesson deck.
_Avoid_: Fifth teaching week, optional demo, final evaluation

**Course Demo**:
The presentation of completed Build Projects after the nine-week course program ends.
_Avoid_: Week 10, final lecture, in-course demo day

## Learning tracks

**AI-Assisted Engineering Practice**:
The disciplined use of AI for code discovery, explanation, planning, and test drafting while the student remains accountable for the resulting work.
_Avoid_: Vibe coding, AI-generated coding, blind prompting

**AI Accountability Note**:
The pull-request statement naming what AI helped with, what the student verified, and what the student remains uncertain about.
_Avoid_: Prompt log, AI disclosure, generated-code report

**Product Engineering Track**:
The course material about changing a shared application safely across its user interface, application boundary, state, integrations, tests, and delivery workflow.
_Avoid_: Traditional half, normal software half

**Agent Engineering Track**:
The course material about model messages, provider behavior, tool use, agent loops, memory, evidence, permissions, and observable agent behavior.
_Avoid_: Agent half, AI magic, chatbot engineering

## Collaboration

**Personal Integration Environment**:
A student's locally configured Sourcecado environment using a Student Model Key and the student's primary Google account through OAuth.
_Avoid_: Shared runtime, shared Google account, production account

**Student Model Key**:
A model-provider credential issued by the course lead to one student with an individual usage limit.
_Avoid_: Shared raw key, student-paid model account, unlimited course key

**Zero-Spend Rule**:
The requirement that no mandatory course activity depends on a paid student account, purchased credits, or another student-funded external action.
_Avoid_: Free trial requirement, reimbursable expense, bring-your-own credits

**Peer Approval**:
An affirmative pull-request review from at least one other student who has inspected the change and its verification evidence.
_Avoid_: Rubber stamp, looks-good approval

**Merge Gate**:
The minimum evidence required before the Course Lead may merge student work: appropriate new or updated automated tests, green CI, and at least one Peer Approval.
_Avoid_: Done, finished locally, tests passed on my machine

**Course Lead Merge**:
The final integration decision made by the course lead after a pull request satisfies the Merge Gate.
_Avoid_: Self-merge, peer merge, direct-to-main

**Live Behavior Demonstration**:
The successful live exercise of changed agent behavior required in addition to automated tests when a pull request changes how the agent acts.
_Avoid_: Formal eval, demo-only verification, looked good once
