import { AlertIcon, KindIcon, SpinnerIcon } from "./AttachmentIcons";
import { AttachmentPreview } from "./AttachmentPreview";
import { describeAttachment, excludedLine, formatBytes, useAttachmentRecord } from "./attachments";
import { useInspector } from "./Inspector";
import { CloseIcon, RetryIcon } from "./icons";
import type { ComposerFile } from "./useComposerFiles";

function stateLine(file: ComposerFile): { readonly text: string; readonly role: "status" | "alert" } {
  if (file.state === "failed") {
    return { text: file.message ?? "This file could not be attached.", role: "alert" };
  }
  return {
    text: `${file.size > 0 ? `${formatBytes(file.size)} · ` : ""}${file.state === "ready" ? "Ready" : "Processing…"}`,
    role: "status",
  };
}

/**
 * The files being attached, above the box: icon, name, size and state, and an
 * icon button that removes the file before the message is sent.
 */
export function ComposerFileChips({
  files,
  onRemove,
}: {
  readonly files: readonly ComposerFile[];
  readonly onRemove: (file: ComposerFile) => void;
}) {
  if (files.length === 0) return null;
  return (
    <ul className="sourcecado-attachment-chips" aria-label="Attached files">
      {files.map((file) => {
        const line = stateLine(file);
        return (
          <li key={file.key}>
            <div className="sourcecado-attachment-chip" data-state={file.state}>
              <span className="sourcecado-attachment-icon">
                {file.state === "processing" ? (
                  <SpinnerIcon />
                ) : file.state === "failed" ? (
                  <AlertIcon />
                ) : (
                  <KindIcon kind={file.kind} />
                )}
              </span>
              <span className="sourcecado-attachment-text">
                <span className="sourcecado-attachment-name" title={file.name}>
                  {file.name}
                </span>
                <span className="sourcecado-attachment-meta" role={line.role}>
                  {line.text}
                </span>
              </span>
              <button
                type="button"
                className="sourcecado-attachment-remove"
                aria-label={`Remove ${file.name}`}
                title="Remove"
                onClick={() => onRemove(file)}
              >
                <CloseIcon />
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function SentFileChip({ id }: { readonly id: string }) {
  const { record, retry } = useAttachmentRecord(id);
  const { select } = useInspector();
  if (record.status === "loading") {
    return (
      <li>
        <div className="sourcecado-attachment-chip" data-state="loading">
          <span className="sourcecado-attachment-icon">
            <SpinnerIcon />
          </span>
          <span className="sourcecado-attachment-text">
            <span className="sourcecado-attachment-name">Loading file…</span>
          </span>
        </div>
      </li>
    );
  }
  if (record.status === "unavailable") {
    // The bytes are gone (for example after a restore from another computer).
    // The chip says so. It is never blank, and it does not open a preview.
    return (
      <li>
        <div className="sourcecado-attachment-chip" data-state="unavailable">
          <span className="sourcecado-attachment-icon">
            <AlertIcon />
          </span>
          <span className="sourcecado-attachment-text">
            <span className="sourcecado-attachment-name">File unavailable</span>
            <span className="sourcecado-attachment-meta">It is no longer on this computer.</span>
          </span>
        </div>
      </li>
    );
  }
  if (record.status === "failed") {
    return (
      <li>
        <button
          type="button"
          className="sourcecado-attachment-chip"
          data-state="failed"
          onClick={retry}
        >
          <span className="sourcecado-attachment-icon">
            <RetryIcon />
          </span>
          <span className="sourcecado-attachment-text">
            <span className="sourcecado-attachment-name">File details did not load</span>
            <span className="sourcecado-attachment-meta">Try again</span>
          </span>
        </button>
      </li>
    );
  }
  const { info } = record;
  // A file the model does not see says so on its chip. It still opens.
  const excluded = excludedLine(info);
  return (
    <li>
      <button
        type="button"
        className="sourcecado-attachment-chip"
        data-state={excluded ? "excluded" : "ready"}
        aria-label={`Open ${info.name}`}
        title={`Open ${info.name}`}
        onClick={(event) =>
          select(
            {
              kind: "file",
              id: info.id,
              title: info.name,
              status: "success",
              card: <AttachmentPreview info={info} />,
            },
            event.currentTarget,
          )
        }
      >
        <span className="sourcecado-attachment-icon">
          {excluded ? <AlertIcon /> : <KindIcon kind={info.kind} />}
        </span>
        <span className="sourcecado-attachment-text">
          <span className="sourcecado-attachment-name">{info.name}</span>
          <span className="sourcecado-attachment-meta" role={excluded ? "note" : undefined}>
            {describeAttachment(info)}
          </span>
          {excluded ? (
            <span className="sourcecado-attachment-meta sourcecado-attachment-excluded">
              {excluded}
            </span>
          ) : null}
        </span>
      </button>
    </li>
  );
}

/**
 * The files on a sent or queued message. A click opens the preview in the
 * inspector. The name and size come from the backend's record of the file,
 * not from the message text.
 */
export function MessageFileChips({ ids }: { readonly ids: readonly string[] }) {
  if (ids.length === 0) return null;
  return (
    <ul className="sourcecado-attachment-chips" aria-label="Attached files">
      {ids.map((id, index) => (
        <SentFileChip key={`${index}:${id}`} id={id} />
      ))}
    </ul>
  );
}
