# Chat attachments

Status: active-stack engineering reference. Issue #253. The full lifecycle (cleanup, DOCX, person files) is #254.

The director can attach files to a chat message. The model reads them as source material. They never change policy.

```
composer  --POST /v1/attachments-->  backend: check, copy, extract
   |                                     |
   |  chip: processing -> ready/failed   v
   |                          <state>/attachments/<id>/{original,text.txt,meta.json}
   v
send: text + "File: file_id <id>." lines      (ids only, never a name or a body)
   |
   v
run_turn: transcript + run ledger keep the lines.
model request: each line becomes a labelled, fenced copy of the file.
```

## What is accepted

PDF, PNG, JPEG, WebP, plain text, Markdown and CSV. Magic bytes decide PDF, PNG, JPEG and WebP. Text, Markdown and CSV must be valid UTF-8 with no NUL byte and a `.txt`, `.md`, `.markdown` or `.csv` name. Everything else is `Unsupported file type`. The name never decides a binary type.

| Limit | Value | Where it is set |
| --- | --- | --- |
| Files per message | 5 | `MAX_FILES_PER_MESSAGE` in `attachments.py` |
| Bytes per file | 10 MB | `MAX_FILE_BYTES` |
| Text per file, for the preview and the model | 30,000 characters | `MAX_TEXT_CHARS` |
| Text from all files in one model request | 120,000 characters, newest message first | `CONTEXT_TEXT_CHARS` |
| PDF pages read | 200, or 20 seconds | `MAX_PDF_PAGES`, `PDF_SECONDS` |

`GET /v1/attachments/limits` returns the numbers. The client shows what it gets. It also says whether the configured model reads images.

A PDF that is encrypted, damaged, or has no text (a scan) is refused with its own message. An image is refused at upload when the model cannot read images, and again when the run starts. Cut text is always labelled, in the preview and in the model request.

## Storage rules

- Bytes live in `<state>/attachments/<id>/`. The id is `att_` plus 32 random hex characters. A pattern check runs before any path is built.
- The file name is a display label in `meta.json`. It is cleaned to its last path component. It is never part of a path, so `../../x` cannot write outside app state.
- Folders are `0700` and files are `0600`. The meta file is written last. A folder without it is not a file.
- `get` returns nothing when the record or the bytes are missing or the size changed. The client then shows `File unavailable`.

## Where the raw text goes, and where it does not

The body is in the attachment folder and in the model request. It is in no other record.

| Record | Holds |
| --- | --- |
| Transcript, session title, queue | the `File: file_id` line only |
| Run ledger goal, checkpoints, receipts | the same line |
| Telemetry, compaction input | the same line (compaction runs before the files are added) |
| Model request | the file text, fenced and labelled with the name |

`attachments.expand_messages` builds the model view after compaction. The nonce in each fence comes from the file id and the text, so the same file gives the same text on every step. That keeps a provider's reasoning continuity intact.

## Policy

File text enters the model inside the evidence fence (`evidence_envelope`), under the evidence policy line. It is admitted to the turn's `ContextAuthority`, so an approval that copies the file's words is marked `evidence_origin: external` and stays at scope `once`. A file cannot grant, widen or skip an approval.

## Tests

`backend/tests/test_attachments.py` covers the store, the request shape, the policy and the "no other record" checks. `frontend/tests/attachments.test.tsx` and `attachmentPreview.test.tsx` cover the composer and the preview.
