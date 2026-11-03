# Person corrections verification — 2026-11-02

Issue: [#243](https://github.com/FisherXZ/Sourcecado/issues/243).
All examples use fictional people, local fake Gmail and fake enrichment. No real
messages, paid lookups or operator state were used.

## Behavior evidence

- The real React/FastAPI editor reviewed company/email changes before saving.
- An independent role edit forced a save conflict. The company/email draft
  survived and its reviewed rebase preserved that concurrent role correction.
- The same person ID, saved task and historical Gmail recipient remained intact.
- Reload retained the correction. Conflicting fake provider facts remained
  visible for review and did not replace human values.
- Field-scoped Undo restored company/email while preserving the other role edit.
- A duplicate email showed the existing file link. Save stayed disabled until
  explicit confirmation; Cancel preserved both files and returned focus.
- The same editor worked in the compact preview. Desktop and narrow screenshots
  used 1440 and 390 px viewports. No document-wide horizontal overflow or browser
  page errors were observed. The narrow-screen drawer was closed and finite responsive navigation
  transitions finished before screenshot capture.

Machine-readable checks: [checks.json](evidence/2026-11-02-person-corrections/checks.json).

## Visual comparison

The selected [Attio reference image](https://a.storyblok.com/f/234930/1686x912/9f355ca443/merge-records.png)
from the issue was inspected. Its useful pattern is a visible comparison of the
consequences before a deliberate decision. Sourcecado adapts that to saved and
corrected person fields, an avocado-tinted proposed-value column, and a separate
possible-duplicate review. It keeps Warm Operator tokens, readable 13 px controls,
icons on the editor's buttons and existing focus styles. It does not copy the
company merge operation or its automatic combination behavior.

| State | 1440 px | 390 px |
| --- | --- | --- |
| Review | [Desktop](evidence/2026-11-02-person-corrections/correction-review-1440.png) | [Narrow](evidence/2026-11-02-person-corrections/correction-review-390.png) |
| Conflicting save | [Desktop](evidence/2026-11-02-person-corrections/correction-conflict-1440.png) | [Narrow](evidence/2026-11-02-person-corrections/correction-conflict-390.png) |
| Duplicate review | [Desktop](evidence/2026-11-02-person-corrections/correction-duplicate-1440.png) | [Narrow](evidence/2026-11-02-person-corrections/correction-duplicate-390.png) |

[Compact editor](evidence/2026-11-02-person-corrections/correction-editor-preview-390.png),
[provider differences](evidence/2026-11-02-person-corrections/provider-differences-1440.png).

## Automated checks

Regressions were observed failing before implementation for duplicate review,
provider protection, explicit chat authority, per-field Undo after snapshot ABA,
review-before-save, retry identity, correction history and focus return. Tests
cover restart, atomic receipt failure, backed-up migration rollback, invalid
values, forged authority and legacy writer bypass. Final suite totals are recorded
with the implementation commit and the combined CRM release report.

## Limits

The browser demonstration uses the actual frontend and local API, not the native
Tauri window. Real provider accuracy and a real send/enrichment exchange were not
performed. Approval execution is verified together with #165 before release;
correction/Undo tests verify the shared monotonic revision contract here. Fisher's
visual review in #212 remains the human release review.
