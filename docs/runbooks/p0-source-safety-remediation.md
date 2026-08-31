# P0 source-safety remediation

Issues: #38 and #39

This runbook contains no credential values. Do not paste old or replacement credentials into GitHub, chat, logs, screenshots, or Google Docs.

## 1. Contain model and event exposure

Deploy the source-boundary hotfix before running another broad Drive ingestion:

- credential assignments and private-key blocks are replaced before tool results leave the Drive client;
- the result records only whether redaction occurred and how many values were removed;
- legal-looking sources are marked `ready_to_use: false` until an authorized review;
- known Codeology/Berkeley Consulting recipient mismatch is marked `party_mismatch`.

## 2. Rotate the Apollo credential

Authorized Apollo operator:

1. Create a replacement credential in Apollo without exposing it in this runbook or an issue comment.
2. Store it only in the approved local secret configuration (`~/.config/club/.env` for the current local app).
3. Restart the backend and verify Apollo reports Connected without printing the value.
4. Run one bounded, read-only Apollo search smoke.
5. Revoke the exposed credential.
6. Verify the revoked credential no longer authenticates using Apollo's own key-management status; do not replay it through logs or shell output.

If Apollo cannot overlap credentials, reverse steps 1 and 5 while keeping the interruption explicit.

## 3. Remove the credential from Drive sources

Authorized Google Drive editor:

1. Replace the credential value in `Codeology Sourcing SOP` with a non-secret instruction such as `[REDACTED — use local secret configuration]`.
2. Search the active sourcing folder for copied values without displaying matches in screenshots or comments.
3. Inspect Google Docs version-history behavior. If the sensitive revision cannot be removed individually, create a clean replacement document, preserve only safe content, quarantine the original, and permanently remove the original only after the authorized owner confirms retention requirements.
4. Update links/bookmarks so operators use the clean canonical SOP.
5. Record the clean document id and remediation timestamp in issue #38 without including credential material.

## 4. Quarantine the stale NDA

Authorized Drive owner:

1. Rename or move the existing file so it is visibly quarantined and cannot be mistaken for an approved template.
2. Do not perform search-and-replace on party names and call the result approved.
3. Obtain a Berkeley Codeology template reviewed by an authorized officer or counsel.
4. Record template version, approval date, and canonical Drive id.
5. Verify the body and signature blocks use the intended recipient plus explicit counterparty placeholders consistently.
6. Keep executed agreements distinct from drafts and templates.

## 5. Verify local residue safely

Search local conversations, events, and logs using a non-printing check derived from the revoked credential. The check should return only matched file counts or paths approved for remediation, never matching lines or values.

Expected post-remediation state:

- no active credential in Drive documents or local event payloads;
- replacement credential present only in secret configuration;
- Drive reads report redaction metadata without raw values;
- stale NDA is quarantined;
- no legal template is marked ready until its review metadata exists.

## 6. Backout

If the containment hotfix over-redacts ordinary prose, disable broad ingestion and revert the code change; do not restore the exposed source value. Keep the Apollo credential revoked and the stale NDA quarantined while refining detection tests.
