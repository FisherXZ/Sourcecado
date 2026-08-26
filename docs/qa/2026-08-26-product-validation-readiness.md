# Product Validation Readiness — 2026-08-26

Branch: `codex/product-validation-sprint`  
Baseline: stacked on desktop-first repository cleanup PR #48  
Mode: read-only connector and runtime preflight; no external writes performed

## Ready

- Sidecar starts and reports the `sourcing` persona on `deepseek-v4-pro`.
- Gmail is connected for the Codeology account.
- Google Drive is connected and reports healthy.
- Google Calendar is connected and reports healthy.
- Apollo is configured.
- Granola is connected.
- `outreach-campaign`, `company-pitch-package`, and `weekly-sourcing` are all
  discoverable through the live `/v1/skills` catalog.
- The scheduler has a weekly sourcing job and two historical receipts.

No credential values were read or printed during this check.

## Gaps already exposed by preflight

1. The connector catalog advertises Drive search/read but not folder listing,
   while the active tool catalog contains `drive_list_folder`. Model, runtime,
   and UI do not yet share one capability contract.
2. Historical scheduler rows still return raw status `ok` instead of canonical
   `success`. This confirms the receipt-truthfulness gap in issue #40.
3. Historical scheduled runs produced no first-class artifacts even when they
   contained complete outreach drafts.
4. The historical weekly run explicitly reported that team memory was not
   writable, while the current person-file and memory tools suggest a newer
   capability. The real workflow must prove which state is authoritative.
5. The existing `weekly-sourcing` skill still says drafts never send. That is
   stale relative to the current approval-gated `gmail_send` product behavior.
6. The scheduler template runs a prompt, but does not explicitly identify a
   skill. The baseline run must prove whether scheduled and interactive work
   actually follow the same skill path.
7. No current tool can create or edit a Google Slides/PowerPoint artifact. The
   pitch-package baseline should return an honest partial result rather than
   claiming a deck was edited.

## Provisional live scenarios

These selections are safe for a read-only baseline and are derived from the
existing scheduled sourcing evidence. Fisher can replace either before any
costly or external action.

### Outreach

Fall 2026 project-partner outreach, beginning with a bounded five-contact slice
from the twelve researched companies already named in the sourcing masterdoc.
Search and drafting may be tested; enrichment, draft creation, and sending
remain approval-gated.

### Pitch package

Zoox partnership/project pitch package. The existing scheduled evidence says a
deck exists and a warm contact path through Stella may exist, while no outreach
trail was found. This makes it a strong test of source recovery, template
selection, relationship context, and truthful artifact creation.

## Next action

Run both scenarios through the actual desktop/browser UI. Preserve the live
transcript, tool receipts, approval states, final artifacts or honest partial
artifacts, restart/resume behavior, and manual interventions.
