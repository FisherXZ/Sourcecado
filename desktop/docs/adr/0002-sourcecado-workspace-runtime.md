# Sourcecado owns the workspace filesystem and shell runtime

Status: Accepted — 2026-08-26

## Context

The sourcing director needs to let Sourcecado inspect and change selected local folders, then leave longer tasks running without granting silent authority over the whole Mac. Typed file operations and shell commands have different failure modes: typed writes can enforce path and version invariants, while a shell is opaque and may write, execute code, or use the network.

The application already owns the agent loop, Inbox approvals, local state, and run receipts. Workspace authority must use those seams without introducing another agent runtime or protocol as a foundational dependency.

## Decision

Sourcecado owns the complete implementation under `desktop/coworker/`.

- A Workspace Grant persists an opaque ID, canonical directory, label, read-only or read-write access, filesystem identity, and creation/update/revocation metadata.
- A Workspace Execution Grant is a read-write Workspace Grant with shell authority enabled.
- Typed tools accept a grant ID and relative path. Resolution is root-contained and rejects absolute paths, traversal, symlinks, special files, identity changes, and Sourcecado state.
- Reads and reversible writes run automatically inside their grant. Existing-file writes require the observed SHA-256 hash. Mutations walk from an opened root descriptor with no-follow semantics, revalidate the parent binding, then use a same-directory temporary file, `fsync`, and descriptor-relative atomic replacement.
- Trash, protected files, conflicting overwrites, cross-root moves, shell input, and consequential commands require an Inbox approval.
- Docker is the preferred shell target. The selected workspace is mounted read-write at `/workspace`; eligible additional grants use explicit mounts. The container runs as the operator UID/GID with a read-only root filesystem, dropped capabilities, no-new-privileges, process/memory/CPU limits, a scrubbed environment, no implicit host home, no Sourcecado state, and no Docker socket.
- Docker network access is unrestricted. This is intentional for AFK sourcing work.
- If Docker CLI, daemon, or the configured image is unavailable, Sourcecado may run an approved command directly under the operator account. The UI must say **Not sandboxed** before execution.
- Automatic shell execution is limited to a very small non-recursive metadata allowlist; file content and recursive discovery use the typed filesystem tools. Everything else asks.
- Direct-host allow-always authority is bound to the exact command, resolved working directory, sanitized environment fingerprint, execution target, shell/executable hashes, and every referenced file hash. Wrapper commands, inline code, shell syntax, extra environment values, redacted inputs, and unresolvable arguments are ineligible for permanent approval. Only the digest and safe inspection metadata persist. The grant is visible and revocable in Settings and can satisfy an AFK call only while every component still matches.
- Raw shell arguments remain in an in-memory approval vault only while the backend is alive. Conversation, event, and Inbox persistence contain redacted arguments; an approval resumed without its transient inputs fails closed and must be requested again.
- Shell tasks have durable IDs, process/container ownership metadata, bounded incremental output, and a parent watchdog. Cancellation escalates from TERM to KILL. Startup terminates verifiable survivors before recording `interrupted`; Sourcecado never infers success.
- Workspace receipts are append-only and contain decisions, targets, hashes, actor/run identity, timing, exit state, truncation, and a sanitized summary. Terminal completion appends a correlated terminal receipt. State directories are mode `0700` and state files are mode `0600`; receipts never contain file bodies, raw environment values, terminal secrets, or unbounded output.

## Accepted risks

- The real workspace is mounted read-write. A command that is approved or incorrectly classified as read-only can damage that workspace.
- Unrestricted container networking means an approved command can transmit workspace data.
- Static command classification is conservative but cannot prove shell semantics. Anything not demonstrably read-only asks.
- Direct-host fallback has the operator account's filesystem authority. Exact fingerprinting reduces standing authority but does not make host execution a sandbox.
- A permanent host approval is powerful. Visibility, immutable receipts, exact matching, and revocation are mandatory product behavior.

## Consequences

Sourcecado can do meaningful AFK work inside an operator-selected folder while keeping authority explicit and inspectable. Docker remains optional rather than a startup dependency. The runtime is independently implemented and can change behind Sourcecado's tool, grant, risk, approval, and receipt interfaces.
