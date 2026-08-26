# Sourcecado Desktop UI/UX Improvement Plan

**Status:** Design review complete; ready for engineering review
**Source implementation:** `2026-08-24-pass-3-openworker-gap-implementation.md`
**Design system:** `DESIGN.md` (Warm Operator)
**Scope:** Desktop Tauri + React surface under `desktop/surfaces/gui/`

## Objective

Turn the working desktop buddy into a conversation-first operator interface with clear agent activity, durable navigation, intentional responsive behavior, and review-ready tool outputs. Preserve the Python sidecar, local-first architecture, Warm Operator visual language, and permission model.

## Initial Assessment

The implemented UI starts at **4/10 design completeness**. The visual tokens are coherent, but the screen hierarchy, interaction states, responsive behavior, and component boundaries remain prototype-level.

## Design Decisions

### D1 — Review depth

**Decision:** Run the full seven-pass design review before implementation.

### D2 — Workspace controls

**Decision:** Give connectors/plugins a dedicated top-level page instead of placing them in the conversation rail, a right drawer, or a bottom dock.

**Reference:** The dedicated Plugins page in the ChatGPT desktop app, supplied by Fisher on 2026-08-25.

**Citation correction (2026-08-26 review):** This reference is weaker than it
reads. Grok Bot ships the equivalent surface as a dialog, not a page, so the
supplied precedent does not uniformly support a dedicated route. The decision
still holds, on stronger ground found during review: failure recovery
deep-links to `#/connections/:connectorId`, and a dialog cannot be a
deep-link target.

**Implications:**

- The conversation surface contains the thread, agent activity, composer, and only approvals or failures affecting the current run.
- Connector discovery, connection status, OAuth actions, and connector settings live on a dedicated **Connections** page.
- Scheduled work and skills receive their own top-level destinations rather than sharing a generic utility surface.
- The global navigation must support both conversation history and durable product destinations. This becomes D3.

### D3 — Global navigation shell

**Decision:** Use one unified 232px app rail.

**Structure:**

1. Product identity and New chat.
2. Search / command palette with a visible trigger and Cmd-K shortcut.
3. Durable destinations: Scheduled, Connections, and Skills.
4. Pinned and recent conversation threads.
5. Settings and operator identity pinned to the bottom.

**Rationale:** This matches the supplied desktop reference, uses one navigation axis, and is sufficient for Sourcecado's current product breadth. Do not introduce a second icon rail or horizontal route tabs.

**Visual:** `scratchpad/d3-navigation-shell.html`

### D4 — Agent work detail

**Decision:** Keep concise tool summaries and source chips inline in the conversation. Open a right-side inspector on demand for full arguments, results, citations, timing, and generated artifacts.

**Rules:**

- The transcript remains readable when the inspector is closed.
- Current-run approvals and blocking failures remain inline; they never hide in the inspector.
- The inspector is contextual to the selected tool call, source, or artifact.
- At narrow widths the inspector becomes an overlay sheet rather than shrinking the transcript.

**Visual:** `scratchpad/d4-agent-workspace.html`

### D5 — Composer during an active run

**Decision:** Keep the composer editable while the agent runs. Submitting creates a visible queued message, and the user can stop the active run without losing their draft or queued work.

**Required states:**

- Running status with elapsed time and current high-level activity.
- Stop control available throughout the run.
- Visible queued-message row with edit, reorder, and remove affordances.
- Clear transition when the active run ends and the next queued message begins.
- Failed or cancelled runs preserve unsent drafts and queued messages.

**Runtime implication:** Add cancel and queue commands to the sidecar protocol. assistant-ui supplies the client interaction model and queue adapter, but the sidecar remains authoritative for persistence, ordering, execution, and terminal acknowledgement.

**Visual:** `scratchpad/d5-running-composer.html`

### D6 — Approval-card disclosure

**Decision:** Use progressive disclosure for approvals.

**Default card content:**

- Human-readable action title, never only the raw tool name.
- External account or resource being changed.
- The few fields required to judge the action safely.
- Plain-language explanation of why permission is required.
- **Deny** and **Allow once** actions.
- Expandable full content, tool arguments, and policy details.

**After resolution:** Collapse the card into a durable audit receipt showing the decision, actor, timestamp, and execution outcome.

**Visual:** `scratchpad/d6-approval-card.html`

### D7 — Multi-tool activity and generative UI

**Decision:** Use one subtle collapsed activity block by default, modeled on the supplied Codex/Claude research references. Expanding the block reveals the structured grouped trace from option A.

**Collapsed state:**

- Natural-language label such as **Searched the web**, **Checked connected sources**, or **Prepared Gmail draft**.
- Completion or running state, elapsed time, and disclosure control.
- No individual tool cards competing with the answer.

**Expanded state:**

- Chronological high-level milestones written for humans, not raw chain-of-thought.
- Tool/source rows with result counts and recognizable source identity.
- Expandable search-result list or artifact preview where useful.
- Failed or partial steps clearly marked without hiding successful work.

**Answer state:**

- Render the useful outcome as a domain component when structure improves comprehension: candidate shortlist, Gmail draft, calendar event, or evidence set.
- Use compact citation pills in prose answers.
- Keep raw arguments, full payloads, and low-level logs in the D4 inspector.

**Framework mapping:** Use a framework tool-group primitive collapsed by default, custom tool renderers, source components, and custom generative/data UI renderers.

**Visual:** `scratchpad/d7-generative-tool-ui.html`, refined by the two Codex/Claude research screenshots supplied on 2026-08-25.

### D8 — Partial tool failure recovery

**Decision:** Attach recovery to the failed step inside the expandable activity block.

**Behavior:**

- Preserve successful Apollo, Gmail, or other connector results.
- Mark the activity group and any downstream answer as **Partial**.
- Explain the exact failed source and failure class in plain language.
- Offer the narrowest valid actions: retry the failed step, repair the connection, or continue without that source.
- Keep raw transport errors behind **Details** or in the inspector.
- Retry only the failed step when the tool is idempotent; otherwise require a fresh approval.

**Reference decision:** OpenWorker already anchors retry to the tail error, gives MCP failures **Details** plus **Open Connectors**, and preserves the rest of the collapsed turn. Grok Bot likewise renders transcript-load recovery where the failure occurred. This resolves D8 without another taste decision.

**Visual:** `scratchpad/d8-error-recovery.html`

### Framework constraint — external chat UI components

**Decision:** Do not hand-build the full chat-component runtime. Adopt an external chat UI framework for structured messages, streaming state, tool rendering, approval gates, and generative UI, then apply custom Warm Operator presentation.

**Required framework capabilities:**

- Custom backend or external-store adapter for the Python sidecar and WebSocket protocol.
- Structured message parts for text, tools, sources, approvals, and artifacts.
- Tool lifecycle states and human-in-the-loop approval callbacks.
- Custom renderers for domain components such as candidate lists, Gmail drafts, calendar events, and source evidence.
- Queue and cancel support from D5.
- Accessible primitives that can be styled without replacing `DESIGN.md`.

### D9 — Chat UI framework

**Decision:** Adopt **assistant-ui**, using `ExternalStoreRuntime` and custom Warm Operator components.

**Why assistant-ui:**

- Supports React 18, matching the current Tauri surface.
- Accepts an existing message store and custom backend protocol.
- Provides structured message parts, streaming status, tool UI, approval gates, queue/cancel capabilities, source rendering, and generative/data UI.
- Lets Sourcecado supply custom component renderers without adopting the stock ChatGPT-style theme.

**Integration boundary:**

- Keep the Python sidecar, local session files, permission engine, and WebSocket transport authoritative.
- Add a typed React event store and convert it into assistant-ui `ThreadMessageLike` content parts.
- Use assistant-ui for interaction state and accessible primitives, not model execution, persistence, cloud sync, or connector logic.
- Do not add the Vercel AI SDK, assistant-ui Cloud, shadcn/ui, or Tailwind to the desktop runtime for this pass.

**Why not Prompt Kit:** Its documented setup requires React 19 plus shadcn/ui and Tailwind. It supplies useful visual components but not the state/runtime layer Sourcecado needs. Keep it as a reference library only.

**Fallback:** If the adapter spike cannot represent Sourcecado approvals and persisted tool state without protocol distortion, keep the new component boundaries and implement them against the existing reducer. Do not fall back to the monolithic `App.tsx`.

## Reference Implementation Audit

| Source | Pattern adopted | Sourcecado decision |
|---|---|---|
| assistant-ui `ExternalStoreRuntime` | Existing state and custom message conversion | Keep the sidecar and WebSocket; adapt into structured message parts |
| assistant-ui Tool UI | Loading, result, error, approval, and custom renderer lifecycle | Registry-backed domain renderers and progressive approval cards |
| OpenWorker `IntegrationsView` / `ConnectorsList` | Dedicated page, connected-first grouping, search, per-connector details | D2 dedicated Connections page |
| OpenWorker `Transcript` | Entire agent turn collapsed by default; humanized steps; raw details on demand | D7 quiet activity block with expandable grouped trace |
| OpenWorker `ApprovalCard` | Human title, destination scope, clamped preview, Allow once | D6 progressive-disclosure approval |
| OpenWorker `McpNotice` / retry anchor | Error beside failed context, Details, Open Connectors, narrow retry | D8 context-bound recovery |
| OpenClaw queue components | Persistent queue states, retry, steer, edit, remove, keyboard reorder | D5 editable composer and visible queue |
| OpenClaw tool cards / run grouping | Collapsed semantic tool rows, detail panel, explicit outcomes | D4 inspector plus D7 grouped trace |
| OpenClaw responsive browser tests | Mobile edge-sheet rail, stacked narrow panels, 44px touch actions | Pass 6 responsive rules |
| Grok Bot transcript | Expandable tool rows with `aria-expanded`; transcript retry surface | Keyboard-accessible disclosures and contextual retry |

## Interaction State Coverage

Every state describes what the operator sees. Backend-only behavior is insufficient.

| Feature | Loading / running | Empty | Error | Success | Partial / interrupted |
|---|---|---|---|---|---|
| App boot | Shell and rail render immediately; muted skeleton rows in the active route | First-run welcome with **Start a chat** and **Open Connections** | Full-page recovery with specific failure and **Retry**; never only “socket error” | Restore the last open destination and thread | Restore cached thread, mark live data stale, offer reconnect |
| Thread rail | Stable skeleton titles; New chat stays available | “No conversations yet” plus **New chat** | Inline rail retry without replacing the main route | Recent and pinned groups render with active state | Missing or deleted thread becomes a one-line unavailable row |
| Conversation load | Transcript skeleton shaped like actual messages | Warm role-specific starter prompts; no generic “Ask anything” alone | Contextual transcript error with **Retry** | Messages, citations, and receipts restore identically after restart | Load available history and mark the missing segment |
| Agent turn | Quiet activity summary, elapsed time, Stop, editable composer | Not applicable | Failed step appears inside the activity group with recovery | Final answer promotes once; activity collapses to receipt | Successful steps remain; answer and activity carry **Partial** |
| Tool activity group | Natural-language current milestone; spinner only on active row | Omit the group when no tools ran | Failed row names the source and recovery; raw error hidden | Collapsed “Checked 3 sources · 18s” receipt | “Checked 2 of 3 sources” with successful and failed rows |
| Approval | Card is stable while submitting the decision | Not applicable | Decision failure keeps actions enabled and explains retry | Collapse to actor/timestamp/outcome receipt | Expired or cancelled cards close without implying denial |
| Composer queue | Queue row shows waiting, sending, reconnecting, or applying settings | Queue region is absent | Failed item retains text and offers Retry/Edit/Remove | Row drains without shifting remaining action columns | Cancel preserves queued messages; interrupted send returns to editable state |
| Generative result | Skeleton matches the final domain component | Helpful “No candidates matched” with **Adjust criteria** | Component-level error with fallback text and source link | Structured shortlist, draft, event, or evidence view | Show available rows plus missing-source annotation |
| Connections catalog | Connected and available sections use stable row skeletons | “No connectors match” with **Clear search** | Page-level fetch retry; connector-specific errors stay on that connector | Connected rows first; account and health status visible | Authorizing, scope-missing, degraded, and reconnect-required states |
| OAuth connection | Button becomes **Connecting…** and remains in place | Not applicable | Explain blocked popup, callback mismatch, or missing scope with next action | Return to the same connector detail and announce connected account | Connected account with missing scopes shows **Finish setup** |
| Scheduled runs | Current run row shows activity and next scheduled time remains visible | Template-backed empty state with **Create automation** | Failed run row links to contextual run detail and retry | Durable run receipt with status, duration, and artifacts | Waiting approvals show an Inbox badge and never claim completion |
| Inspector / artifact | Selected row owns a matching inspector skeleton | “Select an activity, source, or artifact” | Inline retry or **Open externally** when supported | Full arguments, result, citations, and artifact preview | Show cached content with stale or truncated badge |

### State principles

- Preserve user work across every recoverable failure.
- Keep recovery beside the failed object rather than in transient toasts.
- Use global banners only for failures that affect the whole application.
- Never convert a partial result into a visually complete result.
- Never stream raw chain-of-thought. Show high-level milestones and tool facts only.

## User Journey Storyboard

| Step | User does | Intended feeling | UI support |
|---|---|---|---|
| 1. Return | Opens Sourcecado | Oriented immediately | Last destination and thread restore; active rail state and thread title agree |
| 2. Start | Opens a thread or New chat | In control | Clear prompt area, relevant starter actions, connected-source status available without blocking |
| 3. Delegate | Sends a sourcing task | Confident it started | User message lands instantly; quiet running block shows elapsed time and current milestone |
| 4. Continue thinking | Types while tools run | Productive, not trapped | Editable composer, visible queue, Stop, Edit, Remove, and Retry controls |
| 5. Review a consequential step | Receives a Gmail draft or calendar approval | Informed and safe | Human action summary, exact affected resource, expandable details, Allow once and Deny |
| 6. Encounter a failure | Drive scope expires | Calm; prior work is safe | Successful sources stay complete; failed step offers Reconnect, Retry, or Continue without |
| 7. Review the result | Reads shortlist or draft | Trusts the evidence | Domain component, citation pills, expandable activity trace, inspector for full provenance |
| 8. Leave and return | Closes or changes threads | Continuity | Session, queue, receipts, and results persist without duplicate events |

### Time horizons

- **First 5 seconds:** Sourcecado reads as a focused work assistant, not a settings dashboard. Active destination, active thread, and next action are obvious.
- **First 5 minutes:** The operator can delegate, inspect progress, queue a correction, approve a safe action, and verify evidence without learning internal tool names.
- **Long-term relationship:** Threads, approvals, scheduled-run receipts, sources, and artifacts form a trustworthy operating history rather than disposable chat logs.

## Information Architecture

```text
┌────────────────── 232px unified app rail ──────────────────┐
│ Sourcecado                                                 │
│ New chat                                                   │
│ Scheduled                                                  │
│ Connections                                                │
│ Skills                                                     │
│                                                            │
│ Pinned threads                                             │
│ Recent threads                                             │
│                                                            │
│ Settings · operator identity                               │
└────────────────────────────────────────────────────────────┘

Conversation destination
┌────────────────────────────────────────────────────────────┐
│ Active thread title · run status · inspect                 │
├────────────────────────────────────────────────────────────┤
│ User and assistant messages                                │
│ Inline tool summaries · source chips                       │
│ Inline approvals and blocking failures                     │
│                                      ┌───────────────────┐ │
│                                      │ optional inspector │ │
│                                      │ details/artifacts  │ │
│                                      └───────────────────┘ │
├────────────────────────────────────────────────────────────┤
│ Sticky composer                                            │
└────────────────────────────────────────────────────────────┘

Connections destination
┌────────────────────────────────────────────────────────────┐
│ Search · connected accounts · available connectors         │
│ Connector details · OAuth actions · status and errors      │
└────────────────────────────────────────────────────────────┘
```

## AI-Slop Risk Review

**Classifier:** App UI. Calm, dense, task-focused.

### Current risks

- Stacked full-width utility cards make the app look assembled from generic dashboard blocks.
- Every assistant response, tool call, approval, connector group, and schedule uses nearly the same rounded container.
- Raw model/tool labels substitute implementation detail for product hierarchy.
- The current empty state is centered copy without a useful action or product-specific structure.
- Adopting assistant-ui's stock thread styling would turn Sourcecado into another ChatGPT clone.

### Locked corrections

- Use assistant-ui primitives and state, but replace its default visual components.
- Reserve cards for real interactive objects: approval, shortlist, draft, event, connector, artifact.
- Render normal assistant prose without a surrounding card; user messages may retain a compact tinted bubble.
- Use one accent, few surface levels, hairline borders, and no decorative gradients or ornamental icons.
- Use humanized action names and Sourcecado domain nouns. Hide SDK, model, and tool identifiers from the default view.
- Keep activity groups subtle and collapsed; rich generative UI belongs to the result, not every intermediate step.

### Litmus checks after plan changes

| Check | Result |
|---|---|
| Product unmistakable in the first screen? | Yes: unified Sourcecado rail and sourcing-specific starter state |
| One strong visual anchor? | Yes: active conversation or dedicated route content |
| Understandable by scanning headings and status rows? | Yes |
| Each section has one job? | Yes |
| Are cards necessary? | Only for interactive or structured objects |
| Does motion improve hierarchy? | Yes, limited to drawers, disclosure, queue movement, and state transitions |
| Premium without shadows? | Yes; borders and typography carry hierarchy |

## Design-System Alignment

The plan stays inside `DESIGN.md`. Framework defaults never override these tokens.

| System token | Application |
|---|---|
| General Sans 400/500/600 | Navigation, composer, assistant prose, domain result components |
| Geist Mono 400/500 | Status, elapsed time, source counts, identifiers inside expanded technical details |
| Canvas `#FAF8F3` | App and conversation background |
| Surface `#FEFDFB` | Composer, interactive cards, inspector |
| Raised `#F5F2EA` | Hover rows, collapsed activity, neutral receipts |
| Accent `#5B8C2A` | Active nav, primary action, focus, running/success indication |
| Pit `#C2703D` | Needs-attention markers only |
| Border `#E7E3DA` | Pane division and component boundaries |
| Radius 8 / 6 / 4 / pill | Panels and inputs / buttons / tags / status pills |
| 4px spacing system | Every component and responsive layout |

### Component-specific rules

- Assistant prose width: 64–72ch, 14–16px depending on viewport.
- User bubble: maximum 72% desktop, 88% narrow; avocado tint, no saturated fill.
- Activity receipt: borderless or one hairline, 12–13px, collapsed by default.
- Approval: warning tint plus warm border; primary Allow once remains avocado, never warning orange.
- Inspector: surface background and border; shadow only while overlaying the conversation.
- Connection icons use official product marks. Do not place them inside decorative colored circles.

## Responsive and Accessibility Specification

### Viewports

| Width | Global rail | Conversation | Inspector | Connections |
|---|---|---|---|---|
| 1180px+ | Fixed 232px | Centered reading lane with flexible margins | 340–380px right panel or overlay | Two-column connector list where rows remain readable |
| 768–1179px | Hidden behind a persistent rail button; opens as a full-height edge sheet | Full available width | Overlay sheet; never permanently narrows chat | One-column list; detail replaces list with breadcrumb |
| 375–767px | Full-height, viewport-bounded edge sheet | One-pane thread; 16px prose and composer input | Full-screen sheet with clear Back/Close | One-column, 44px rows/actions, sticky search |
| Short landscape | Edge sheet | Transcript remains visible; composer gets an internal bounded scroll region | Full-screen if opened | One-column |

### Accessibility requirements

- All interactive targets are at least 44×44px on touch viewports.
- Every route has one `h1`; the active navigation item uses `aria-current="page"`.
- Thread rail is a labeled navigation region; transcript is a focusable `role="log"` with controlled announcements.
- Do not place `aria-live` on the entire streaming transcript. Announce run start, approval required, failure, cancellation, and completion through a dedicated polite status region.
- Activity and technical disclosures use native `details/summary` or buttons with `aria-expanded` and `aria-controls`.
- Approval actions are reachable in document order; focus moves to the approval title when it appears and returns to the composer after resolution.
- Queue reorder supports pointer and keyboard controls. Never require drag alone.
- Session rename lives in an overflow menu plus inline editor. Double-click may remain a shortcut but never the only path.
- Provide visible `focus-visible` rings using the avocado accent.
- Respect `prefers-reduced-motion`; animate only opacity and transform.
- Maintain WCAG AA contrast and 16px text inputs on narrow/touch layouts.
- Truncate long session, connector, and tool names with accessible full labels or titles.

## Unresolved Design Decisions

No unresolved design decisions remain after the user selections and reference-code audit.

### Auto-decided from references

| Decision | Resolution | Evidence |
|---|---|---|
| D8 partial failure | Context-bound recovery | OpenWorker retry anchor and MCP notice; Grok Bot transcript retry |
| Framework | assistant-ui `ExternalStoreRuntime` | User choice plus assistant-ui custom-runtime and Tool UI capabilities |
| Connector terminology | **Connections** route, connector objects inside | Existing Sourcecado domain language and OpenWorker's explicit Integrations → Connectors rename |
| Tool activity default | Collapsed one-line receipt; expand grouped steps | User Codex/Claude references plus OpenWorker turn grouping |
| Narrow navigation | Full-height edge sheet | OpenClaw responsive behavior and browser tests |
| Inspector on narrow screens | Overlay/full-screen sheet | OpenClaw side-panel stacking and D4 choice |
| Theme | Follow system, with user override later | Existing light and dark token sets in `DESIGN.md` |
| Model selector | Keep out of the primary composer in v1 | One-model desktop wedge; model selection is settings-level configuration |

## Two High-Level Implementation Directions

### Direction A — assistant-ui spine, Sourcecado presentation (chosen)

Use assistant-ui for thread state, message parts, streaming lifecycle, queue/cancel behavior, tool UI registration, approvals, source parts, and generative UI. Sourcecado owns routing, the unified rail, the sidecar adapter, domain components, and all Warm Operator styling.

**Advantages:** Mature interaction semantics, accessible primitives, less custom chat-state code, and a direct path to richer agent tools.

**Risk:** A careless adapter could duplicate sidecar state or distort persisted events. Mitigate with a contract-first spike and fixture-based parity tests before replacing the current transcript.

### Direction B — custom component refactor on the current reducer (fallback only)

Keep the existing event reducer and implement the same component architecture without assistant-ui.

**Advantages:** Minimal dependency change and full protocol control.

**Risk:** Sourcecado continues owning queueing, cancelation, accessibility, tool-part composition, and every future chat interaction. This is the fallback only if the assistant-ui spike fails its go/no-go checks.

## Component and Runtime Architecture

```text
Python sidecar
  session HTTP + typed WebSocket events
              │
              ▼
SourcecadoChatStore
  canonical thread id, persisted messages, live parts, queue, approvals
              │
              ▼
assistant-ui ExternalStoreRuntime
  message conversion + capability callbacks
              │
      ┌───────┴────────────────────────────────────────────┐
      ▼                                                    ▼
Warm Operator thread primitives                    Domain renderer registry
message, composer, activity, sources               Apollo · Gmail · Calendar
approval, queue, inspector hooks                   Drive · Granola · generic
```

### Proposed frontend structure

```text
desktop/surfaces/gui/src/
├── App.tsx                              # app bootstrap only
├── app/
│   ├── AppShell.tsx                     # unified rail + route outlet
│   ├── route.ts                         # hash routes; active destination/thread
│   └── GlobalRail.tsx                   # destinations + pinned/recent threads
├── chat/
│   ├── SourcecadoRuntimeProvider.tsx    # assistant-ui ExternalStoreRuntime
│   ├── store.ts                         # sidecar-authoritative event reducer
│   ├── messageAdapter.ts                # StoredMessage/ChatEvent → message parts
│   ├── threadAdapter.ts                 # session list/open/create/rename
│   ├── toolRegistry.tsx                 # framework-backed tool renderers
│   ├── ThreadView.tsx
│   ├── ThreadHeader.tsx
│   ├── AssistantMessage.tsx
│   ├── UserMessage.tsx
│   ├── ActivityGroup.tsx
│   ├── ApprovalCard.tsx
│   ├── Queue.tsx
│   ├── Composer.tsx
│   ├── SourceCitation.tsx
│   └── Inspector.tsx
├── generative/
│   ├── ApolloPeopleResult.tsx
│   ├── GmailDraftResult.tsx
│   ├── CalendarEventResult.tsx
│   ├── DriveEvidenceResult.tsx
│   └── GenericToolResult.tsx
├── routes/
│   ├── ConnectionsPage.tsx
│   ├── ScheduledPage.tsx
│   ├── SkillsPage.tsx
│   └── SettingsPage.tsx
└── styles/
    ├── tokens.css
    ├── shell.css
    ├── chat.css
    └── responsive.css
```

### Route model

- `#/chat/:sessionId`
- `#/scheduled`
- `#/connections`
- `#/connections/:connectorId`
- `#/skills`
- `#/settings`

Hash routing avoids a new router dependency and keeps browser-dev and Tauri navigation deterministic.

### Sidecar contract additions

- Versioned event envelope carrying `session_id`, `run_id`, `event_id`, and stable message/part ids across live streaming and restored history.
- Structured content parts for text, tool call, tool result, approval, source, artifact, and notice.
- Turn lifecycle: start, active milestone, partial, stopped, failed, complete.
- Cancel command with terminal acknowledgement.
- Sidecar-persisted queue commands: add, edit, move, remove, retry, optional steer.
- Approval receipt with decision, actor, timestamp, scope, and outcome.
- Source references and artifact metadata survive session reload.
- A failed step identifies whether retry is safe and which connection route repairs it.
- Backward-read adapter for existing role/content/tool JSONL entries. Do not rewrite old conversation files during the UI migration.
- Route every live event by session and run identity so switching threads cannot append deltas to the wrong transcript.

## What Already Exists

- Warm Operator colors, typography, spacing, radius, and motion rules in `DESIGN.md`.
- Tauri/Vite/React desktop surface and local token handoff.
- Session list, create, restore, rename, and per-session chat APIs.
- WebSocket events for turn start, assistant deltas, tools, approvals, completion, and error.
- Persisted conversations, tool results, inbox approvals, schedules, and memory.
- Connector status and OAuth endpoints for Gmail, Drive, Calendar, Apollo, and Granola.
- Permission policy with safe reads, asked writes, and denied unknown actions.
- OpenWorker reference components for connectors, approvals, Markdown, grouped turns, and retry.
- OpenClaw reference components for queueing, run frames, tool details, responsive rails, and accessibility.
- Grok Bot reference components for transcript disclosures, retry, and message actions.

Reuse these contracts and patterns. Do not rebuild the sidecar, permission engine, or storage model to fit a frontend library.

## NOT in Scope

- New Gmail sending capability. `gmail_send` already shipped in commit 8ed9cb7
  and is approval-gated; this pass adds no send path. It does correct that
  tool's approval card, which previously rendered a generic card falsely
  stating that Sourcecado would not send email.
- New connectors or connector backend rewrites.
- assistant-ui Cloud, hosted thread persistence, or cloud authentication.
- Vercel AI SDK, shadcn/ui, Tailwind, or React 19 migration.
- Voice input, attachments, message branching, edit/regenerate, or feedback reactions.
- Multi-agent teams, board, or OpenWorker persona/workspace breadth.
- Mobile-native application work; responsive browser/Tauri narrow-window behavior is in scope.
- A new visual identity, new color palette, or decorative redesign.
- Exposing raw chain-of-thought.
- Auto-resuming scheduled runs after an Inbox approval; retain the current explicit follow-up behavior.

## Implementation Tasks

Synthesized from the design review. Check each task as it ships.

Machine-readable mirror: `scratchpad/tasks-design-review-20260825.jsonl`.

- [ ] **T1 (P1, human: ~4h / Codex: ~20min)** — Shell — Build hash routing and the unified app rail.
  - Surfaced by: Pass 1, D2/D3.
  - Files: `desktop/surfaces/gui/src/App.tsx`, `desktop/surfaces/gui/src/app/AppShell.tsx`, `desktop/surfaces/gui/src/app/route.ts`, `desktop/surfaces/gui/src/app/GlobalRail.tsx`, route page stubs.
  - Verify: New chat, route selection, active thread, browser refresh, and last-open restoration.

- [ ] **T2 (P1, human: ~1 day / Codex: ~45min)** — Runtime — Prove assistant-ui `ExternalStoreRuntime` against recorded Sourcecado fixtures.
  - Surfaced by: D9 framework decision.
  - Files: `desktop/surfaces/gui/src/chat/SourcecadoRuntimeProvider.tsx`, `desktop/surfaces/gui/src/chat/store.ts`, `desktop/surfaces/gui/src/chat/messageAdapter.ts`, fixture tests.
  - Verify: Restored and live conversations render identically; no duplicate deltas, tools, or receipts.
  - Go/no-go: approvals, persisted tools, partial failures, and thread switching must round-trip without changing sidecar authority.

- [ ] **T3 (P1, human: ~1 day / Codex: ~45min)** — Protocol — Add stable structured parts, cancel, and queue commands.
  - Surfaced by: D5 and interaction-state table.
  - Files: `desktop/coworker/server.py`, `desktop/coworker/turn.py`, `desktop/coworker/store.py`, `desktop/surfaces/gui/src/api.ts`, runtime store and tests.
  - Verify: Stop acknowledges terminal state; queued messages persist, edit, reorder, remove, retry, and drain once; every event routes by session/run id.

- [ ] **T4 (P1, human: ~1 day / Codex: ~45min)** — Thread — Replace raw bubbles with custom assistant-ui message and Markdown primitives.
  - Surfaced by: Current raw Markdown rendering and Pass 4.
  - Files: `desktop/surfaces/gui/src/chat/ThreadView.tsx`, `desktop/surfaces/gui/src/chat/AssistantMessage.tsx`, `desktop/surfaces/gui/src/chat/UserMessage.tsx`, `desktop/surfaces/gui/src/chat/Composer.tsx`, chat styles.
  - Verify: GFM tables, lists, code, links, long drafts, streaming, copy, and restored history.

- [ ] **T5 (P1, human: ~1.5 days / Codex: ~60min)** — Agent activity — Implement the collapsed activity group and domain renderer registry.
  - Surfaced by: D7 and reference audit.
  - Files: `desktop/surfaces/gui/src/chat/ActivityGroup.tsx`, `desktop/surfaces/gui/src/chat/toolRegistry.tsx`, `desktop/surfaces/gui/src/generative/*`, message adapter.
  - Verify: Running, completed, failed, partial, denied, and restored tool sequences; collapsed by default.

- [ ] **T6 (P1, human: ~1 day / Codex: ~45min)** — Trust — Implement progressive approvals and contextual recovery.
  - Surfaced by: D6/D8.
  - Files: `desktop/surfaces/gui/src/chat/ApprovalCard.tsx`, `desktop/surfaces/gui/src/generative/GenericToolResult.tsx`, source/connection recovery components, protocol tests.
  - Verify: Allow once, deny, expired, cancelled, submission failure, resolved elsewhere, and audit receipt states.

- [ ] **T7 (P2, human: ~1 day / Codex: ~45min)** — Inspector — Add on-demand tool, source, citation, and artifact detail.
  - Surfaced by: D4.
  - Files: `desktop/surfaces/gui/src/chat/Inspector.tsx`, `desktop/surfaces/gui/src/chat/SourceCitation.tsx`, source/artifact adapters, shell integration.
  - Verify: Selected detail stays synchronized; overlay and full-screen modes preserve focus and scroll.

- [ ] **T8 (P2, human: ~1 day / Codex: ~45min)** — Connections — Replace connector strips with a dedicated connected-first catalog and detail route.
  - Surfaced by: D2 and user reference.
  - Files: `desktop/surfaces/gui/src/routes/ConnectionsPage.tsx`, connector row/detail components, route integration.
  - Verify: Search, empty search, connected, available, authorizing, scope-missing, failed, reconnect, and disconnect.

- [ ] **T9 (P2, human: ~1 day / Codex: ~45min)** — Scheduled/Skills — Promote existing data into dedicated routes and rail badges.
  - Surfaced by: D3 and reference audit.
  - Files: `desktop/surfaces/gui/src/routes/ScheduledPage.tsx`, `desktop/surfaces/gui/src/routes/SkillsPage.tsx`, `desktop/surfaces/gui/src/app/GlobalRail.tsx`.
  - Verify: Empty templates, next run, run receipts, waiting approvals, failures, and selected route restoration.

- [ ] **T10 (P2, human: ~1 day / Codex: ~60min)** — Responsive/a11y — Land edge-sheet navigation, narrow inspector, keyboard paths, and announcement policy.
  - Surfaced by: Pass 6.
  - Files: `desktop/surfaces/gui/src/styles/responsive.css`, disclosure/queue/rail components, accessibility tests.
  - Verify: 375×812, 768×1024, 1024×768, 1280×720, and 1440×900; keyboard-only flow and reduced motion.

- [ ] **T11 (P2, human: ~4h / Codex: ~25min)** — Cleanup — Remove the legacy monolithic rendering path after parity tests pass.
  - Surfaced by: current 590-line `App.tsx` and duplicate state/render responsibilities.
  - Files: `desktop/surfaces/gui/src/App.tsx`, `desktop/surfaces/gui/src/styles.css`, obsolete format helpers.
  - Verify: production build, full desktop pytest, component tests, and visual regression baseline.

## Verification Plan

### Automated

- Python: `.venv/bin/pytest -q` from `desktop/`.
- Frontend build: `npm run build` from `desktop/surfaces/gui/`.
- Add Vitest + Testing Library inside the GUI package for adapters and component states.
- Contract fixtures cover live and restored forms of every structured message part.
- Queue property tests cover ordering, cancellation, retries, reconnect, and duplicate acknowledgements.
- Accessibility tests cover names, roles, expanded state, focus return, and keyboard reorder.

### Visual and interaction QA

- Capture before/after screenshots at 375, 768, 1024, 1280, and 1440 widths.
- Walk: restore thread → run multi-tool search → queue correction → approval → partial connector failure → reconnect → rich result → inspector.
- Walk: create and rename thread using keyboard-accessible menus.
- Walk: Connections search, OAuth success/failure, missing scope, and disconnect.
- Confirm no horizontal overflow, hidden recovery actions, raw Markdown, raw JSON by default, or sub-44px touch actions.

## TODOS.md Updates

None. All accepted UI debt is captured as T1–T11 in this plan. Deferred feature expansion is listed under **NOT in Scope** rather than added as ambiguous backlog debt.

## Review Progress

| Pass | Dimension | Initial score | Status |
|---|---|---:|---|
| 1 | Information architecture | 3/10 → 10/10 | Complete |
| 2 | Interaction states | 3/10 → 10/10 | Complete |
| 3 | User journey | 4/10 → 10/10 | Complete |
| 4 | AI-slop risk | 5/10 → 10/10 | Complete |
| 5 | Design-system alignment | 7/10 → 10/10 | Complete |
| 6 | Responsive and accessibility | 1/10 → 10/10 | Complete |
| 7 | Unresolved decisions | 8 found → 0 open | Complete |

## Approved HTML Decision Artifacts

| Decision | HTML | Approved direction |
|---|---|---|
| D3 global navigation | `scratchpad/d3-navigation-shell.html` | Unified app rail |
| D4 agent workspace | `scratchpad/d4-agent-workspace.html` | Inline summaries plus on-demand inspector |
| D5 active-run composer | `scratchpad/d5-running-composer.html` | Editable composer, queue, and Stop |
| D6 approvals | `scratchpad/d6-approval-card.html` | Progressive disclosure |
| D7 generative tool UI | `scratchpad/d7-generative-tool-ui.html` | Quiet collapsed block; expanded grouped trace |
| D8 recovery | `scratchpad/d8-error-recovery.html` | Context-bound recovery |

## Design Plan Review — Completion Summary

| Area | Result |
|---|---|
| System audit | `DESIGN.md` exists; UI scope is the desktop Tauri/React surface |
| Initial score | 4/10 design completeness |
| Final score | 10/10 design completeness |
| Information architecture | Unified rail, dedicated routes, conversation-first center, optional inspector |
| Interaction states | Full loading, empty, error, success, partial, cancel, queue, and approval coverage |
| User journey | Return → delegate → monitor → approve → recover → verify → resume |
| AI-slop risk | Framework defaults rejected; custom Warm Operator primitives required |
| Design-system alignment | Existing typography, colors, density, radii, and motion mapped to components |
| Responsive/a11y | Desktop, tablet, narrow browser, touch targets, keyboard, announcements specified |
| Decisions made | 9 durable decisions including assistant-ui adoption |
| Decisions deferred | 0 design decisions; feature expansion is explicitly out of scope |
| TODO proposals | 0; implementation work lives in T1–T11 |
| HTML artifacts | 6 decision boards, all responsive and browser-verified |

The plan is design-complete. Run an engineering review before implementation, then perform a live design review after the new surface is built.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|---|---|---|---:|---|---|
| CEO Review | `/plan-ceo-review` | Scope and strategy | 0 | — | Existing desktop product direction treated as locked context |
| Codex Review | `/codex review` | Independent second opinion | 0 | — | Not requested |
| Eng Review | `/plan-eng-review` | Architecture and tests (required) | 0 | REQUIRED | Run after this design plan becomes the implementation-planning input |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | CLEAR (SELF-REVIEW) | Score 4/10 → 10/10; 9 decisions; 11 implementation tasks |
| DX Review | `/plan-devex-review` | Developer-experience gaps | 0 | — | Not required for the design pass |

**VERDICT:** DESIGN CLEARED — the UI/UX plan is ready for engineering implementation planning; engineering review remains required before implementation.

NO UNRESOLVED DECISIONS
