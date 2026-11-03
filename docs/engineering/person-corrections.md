# Protected person corrections

The preview and Person File use the same `PersonDetailsEditor`. It edits first
and last name, role, company, email, phone and LinkedIn profile. Review shows the
saved and proposed values before Save. Cancel returns focus to Edit details.
Failed or conflicting saves keep the draft. Reviewing the latest saved version
rebases only fields the director actually edited, preserving unrelated changes.
A retry keeps the same operation ID and returns its original receipt.

`PersonCorrectionService` validates values and writes the same person row. Person
IDs, tasks, descriptions, handoff, existing Gmail records and source pointers
remain attached. Email, phone or LinkedIn collisions produce a comparison and a
link to each existing file. Saving requires confirmation of the exact current
collision set. Name similarity does not select, change or combine files.

Each corrected field has its own revision and human protection in
`person_field_controls`. Apollo enrichment and repeated keeps filter their
incoming facts through `_filter_provider_fields`. Conflicting facts become
reviewable `person_correction_conflicts`; they cannot replace protected values,
including an intentionally cleared field. Enrichment receipts distinguish fields
found, fields actually applied and sourced conflicts. Credit accounting remains
owned by the shared approved-enrichment boundary.

A correction, its per-field authority, snapshot and operation receipt commit in
one transaction. Undo restores only that correction's fields and earlier
protection/source metadata. It checks each field's revision and saved value,
refuses newer edits, and preserves unrelated work. Provider changes and whole
person snapshot restoration cannot make an older correction Undo eligible after
an A→B→A sequence.

The correction and Undo writer call the shared schema-9
`_bump_identity_revision(person_id, before)` helper. Changes to approval-relevant
facts invalidate earlier send/enrichment approval snapshots, including A→B→A.
Title, phone and LinkedIn corrections do not invalidate approvals whose match
contract does not depend on those fields. Sending/enrichment execution must use
#165's shared consequential-action checks; the combined #165/#243 checks remain
part of #212 release verification.

Chat uses `crm_review_person_correction` for a read-only preview and
`crm_correct_person` for the same writer. Only an exact current director command
can establish human authority, for example “Change Maya's company to Lumen Labs
and email to maya@example.com.” Model-supplied actor flags, conditional wording,
extra fields and a different named person are refused. Duplicate confirmation
must name the exact existing person IDs as well as the correction. Unverified
legacy `board_mutate` identity patches and identity-changing whole-person reverts
cannot bypass this boundary. Other note/target operations retain their existing
behavior.

Schema 10 adds the per-field controls and provider-conflict tables and widens the
shared CRM history constraint. The registered 9→10 migration backs up first,
preserves existing identity facts and work, and rolls back interrupted DDL.
Migration itself does not enrich, import mail, merge records or call a model.
