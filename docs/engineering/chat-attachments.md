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

PDF, PNG, JPEG, WebP, plain text, Markdown and CSV. Magic bytes decide PDF, PNG, JPEG and WebP. Text, Markdown and CSV need a `.txt`, `.md`, `.markdown` or `.csv` name and bytes that read as text: UTF-8, UTF-16 with a byte order mark, or Windows-1252 (what Excel writes as "CSV (Comma delimited)"). A text file in another encoding, or a file with NUL bytes or many control characters, is refused. Everything else is `Unsupported file type`. The name never decides a binary type.

| Limit | Value | Where it is set |
| --- | --- | --- |
| Files per message | 5 | `MAX_FILES_PER_MESSAGE` in `attachments.py` |
| Bytes per file | 10 MB | `MAX_FILE_BYTES` |
| Bytes per image | 5 MB | `MAX_IMAGE_BYTES` |
| Pixels on one side of an image | 8,000 | `MAX_IMAGE_SIDE` |
| Text per file, for the preview and the model | 30,000 characters | `MAX_TEXT_CHARS` |
| Text from all files in one model request | 150,000 characters (5 files of 30,000), newest message first | `CONTEXT_TEXT_CHARS` |
| PDF read | 200 pages, 15 seconds, 768 MB of memory, 2 MB per decompressed content stream, 2 reads at once | `pdf_isolation.py` |

The image limits are the strictest documented provider limits (Anthropic: 5 MB and 8,000 pixels a side per image; OpenAI documents 20 MB). One limit for all providers means a failover never meets a limit the first provider did not. They come from provider documentation. No live call has confirmed them.

`GET /v1/attachments/limits` returns the numbers. The client shows what it gets. It also says whether the configured model reads images.

Each refusal has its own message: an encrypted, damaged or too complex PDF, a PDF with no text (a scan), a PDF that took too long, an image that is damaged, too large or too wide, and an image for a model that cannot read images. Cut text is always labelled, in the preview and in the model request.

## Images

`image_headers.py` reads each image the way a decoder reaches its pixels, without decoding them: every PNG chunk and its check value, every JPEG segment up to the scan and the end marker, the WebP size and header. A file with a PNG, JPEG or WebP magic number that is not a whole image is refused at upload, so a provider never gets it.

If a provider still answers HTTP 400 to a request that held an image, the run stops with a message that names the image. The image is marked `provider_rejected` in its folder (`state.json`). Every later request leaves it out and says so to the model. A new send that names it stops with a message. The thread keeps working. The file stays, so its chip and preview still open.

## PDF reads

A PDF is hostile input. `pypdf` can spend minutes of CPU, and hundreds of megabytes, on a few kilobytes: a small compressed stream that expands to megabytes of text operators, shared by many pages. The read runs in a child process (`pdf_worker.py`, started by `pdf_isolation.py`):

- A page whose content stream expands past 2 MB ends the read at once with "too complex". Each stream is measured once, so pages that share a stream cost one measure.
- The parent kills the child after 15 seconds, or above 768 MB resident, or when the upload is dropped (the client closed the request, or the chip was removed). It kills the whole process group.
- The child gets no environment but `PATH`, so no key reaches it.
- A packaged build has no `python -m`. The frozen backend runs itself with `--pdf-worker`. `packaging/backend_entry.py` handles the flag. This path is covered by a test of the entry point, and has not been run from a built app.

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
| Model request | the file name and the file text, both inside the fence |

`attachments.expand_messages` builds the model view after compaction. The nonce in each fence comes from the file id and the text, so the same file gives the same text on every step. That keeps a provider's reasoning continuity intact.

## What a request leaves out

A request carries up to 150,000 characters of file text, newest message first. One message at its limits always fits, so a single send never loses a file. Older files can be pushed out when a thread grows. The model is told, and the file is marked `left_out` in its folder. The chip on that message says so ("Left out of the model's view"). The mark lifts when a later request has room again. A chip shows the mark when its record is next read, which is the next time the thread loads.

## Policy

File text and the file name both enter the model inside the evidence fence (`evidence_envelope`), under the evidence policy line. The words around the fence say only "File 2 of 3 (PDF, 2 pages, 1 KB)". Each file is admitted to the turn's `ContextAuthority`, so an approval that copies the file's words or its name is marked `evidence_origin: external` and stays at scope `once`. A file cannot grant, widen or skip an approval.

Known display gap: `approval_resolved` shows the scope the director clicked, even when the run clamped it to `once`.

## Tests

`backend/tests/test_attachments.py` covers the store, the request shape, the policy and the "no other record" checks. `frontend/tests/attachments.test.tsx` and `attachmentPreview.test.tsx` cover the composer and the preview.
