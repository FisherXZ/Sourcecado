import { currentChannel, isPreview, type ChannelDescriptor } from "../updateChannel";

/**
 * The release channel, shown two ways.
 *
 * `PreviewChannelBadge` is persistent chrome that only a preview build renders,
 * so an operator can never be unsure which build is in front of them.
 * `ReleaseChannelSettings` is the full explanation, including what to do when an
 * update goes wrong.
 *
 * Neither one can change the channel, because nothing in Sourcecado can. See
 * `src/updateChannel.ts` for why that is the design rather than a missing feature.
 */

export function PreviewChannelBadge({
  descriptor = currentChannel(),
}: {
  descriptor?: ChannelDescriptor;
}) {
  if (!isPreview(descriptor)) return null;
  return (
    <div className="preview-channel-badge" role="status" aria-label="Release channel">
      <span className="preview-channel-dot" aria-hidden="true" />
      <strong>Preview build</strong>
      <code>{descriptor.version}</code>
    </div>
  );
}

export function ReleaseChannelSettings({
  descriptor = currentChannel(),
}: {
  descriptor?: ChannelDescriptor;
}) {
  const preview = isPreview(descriptor);
  return (
    <section className="settings-section" aria-labelledby="release-channel-heading">
      <h2 id="release-channel-heading">Release channel</h2>
      <p className="settings-status">
        <span className={`channel-pill channel-pill-${descriptor.channel}`}>
          {descriptor.label}
        </span>
        <span>{descriptor.summary}</span>
      </p>

      <dl className="settings-facts">
        <div>
          <dt>Version</dt>
          <dd>
            <code>{descriptor.version}</code>
          </dd>
        </div>
        <div>
          <dt>Build</dt>
          <dd>
            <code>{descriptor.commit}</code>
          </dd>
        </div>
      </dl>

      <h3>{preview ? "What is different on this channel" : "About the preview channel"}</h3>
      <ul className="channel-differences" aria-label="Channel behaviour">
        {descriptor.differences.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>

      {preview && (
        <div className="channel-rollback">
          <strong>If an update goes wrong</strong>
          <p>
            Sourcecado keeps the version you were on beside the new one, as
            <code> Sourcecado.app.previous</code>, and keeps a backup of your local
            data taken just before the update. A failed update puts both back on
            its own and tells you it did.
          </p>
          <p>
            To go back after an update that installed cleanly, quit Sourcecado
            first, then follow the rollback steps in the preview channel notes.
            Rolling back while Sourcecado is running is the one thing that can
            lose work.
          </p>
        </div>
      )}
    </section>
  );
}
