import type { PendingSendApproval } from "../api";
import { Glyph } from "../generative/ResultCard";
import { Icon } from "../icons";

/**
 * The once-only send decision for one reviewed Gmail draft. It names the
 * subject, recipient and account the approval covers. When the draft on
 * screen no longer matches the approved version, it says that Allow sends
 * nothing.
 */
export function SendApproval({
  approval,
  reviewed,
  busy,
  where,
  onAllow,
  onDeny,
}: {
  approval: PendingSendApproval;
  /** The draft on screen is the version this approval covers. */
  reviewed: boolean;
  busy: string | null;
  /** Where the email goes, such as "in the thread above". */
  where?: string;
  onAllow: () => void;
  onDeny: () => void;
}) {
  const { subject, to, account } = approval.resource;
  return (
    <div className="crm-approval person-outreach-approval" role="group" aria-label="Send approval">
      <p className="crm-approval-head">
        <Icon name="approve" />
        <strong>Send once</strong>
      </p>
      <p className="crm-approval-summary">
        “{subject}” to <strong>{to}</strong> from {account ?? "the connected account"}
        {where ? ` ${where}` : ""}, exactly as shown.
      </p>
      {reviewed ? null : (
        <p className="crm-alert" role="alert">
          <Glyph name="info" />
          This draft changed after approval was requested, so allowing now sends nothing.
          Re-read it and ask again.
        </p>
      )}
      <div className="crm-actions">
        <button type="button" className="crm-btn is-primary" disabled={busy !== null} onClick={onAllow}>
          <Icon name="send" />
          {busy === "allow" ? "Sending…" : "Allow once and send"}
        </button>
        <button type="button" className="crm-btn" disabled={busy !== null} onClick={onDeny}>
          <Icon name="deny" />
          {busy === "deny" ? "Denying…" : "Deny"}
        </button>
      </div>
    </div>
  );
}

/** "Version 1a2b3c4d5e6f": the reviewed body the approval binds to. */
export function DraftVersion({ digest }: { digest: string }) {
  return (
    <span className="crm-version person-outreach-version" title="Approval covers this exact version">
      <Glyph name="keep" />
      Version <code>{digest.slice(0, 12)}</code>
    </span>
  );
}
