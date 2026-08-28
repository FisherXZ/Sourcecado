/**
 * Which release channel this build is on, and what that changes for the operator.
 *
 * The channel is a property of the build, not a setting. You join the preview
 * channel by installing a preview build, and there is no control anywhere in
 * Sourcecado that switches you onto it. That is the whole opt-in design: the
 * sidecar trusts a different signing key per channel, so a stable installation
 * cannot authenticate a preview update even if it is handed one. A toggle would
 * be one mis-click away from putting an operator on a channel they did not
 * choose; an install is not.
 *
 * An unknown or missing channel reads as stable. A build that cannot say what
 * it is gets the more conservative of the two answers.
 */

export type ReleaseChannel = "stable" | "preview";

export type ChannelDescriptor = {
  channel: ReleaseChannel;
  label: string;
  version: string;
  commit: string;
  summary: string;
  differences: string[];
};

const UNKNOWN_VERSION = "unknown";

const PREVIEW_DIFFERENCES = [
  "Updates are installed by hand. Sourcecado never updates itself in the background.",
  "Sourcecado backs up your local data before an update changes it, and puts the backup back if the update fails.",
  "An update waits for work that is still running. If a send has gone out and has not reported back yet, the update stops instead of restarting Sourcecado.",
  "Preview builds are signed for the preview channel only. A stable update cannot be installed over this build.",
];

const STABLE_DIFFERENCES = [
  "This build installs stable updates only. It cannot verify a preview update, so it will refuse one.",
  "To join the preview channel, download a preview build from the release page and install it yourself.",
  "Sourcecado never changes your channel on its own.",
];

export function readChannel(raw: unknown): ReleaseChannel {
  return raw === "preview" ? "preview" : "stable";
}

function text(raw: unknown, fallback: string): string {
  const value = typeof raw === "string" ? raw.trim() : "";
  return value || fallback;
}

/** Build the descriptor from raw build-time values. Pure, so it is testable. */
export function describeChannel(
  rawChannel: unknown,
  rawVersion: unknown,
  rawCommit: unknown,
): ChannelDescriptor {
  const channel = readChannel(rawChannel);
  const preview = channel === "preview";
  return {
    channel,
    label: preview ? "Preview build" : "Stable",
    version: text(rawVersion, UNKNOWN_VERSION),
    commit: text(rawCommit, UNKNOWN_VERSION).slice(0, 12),
    summary: preview
      ? "This is a preview build of Sourcecado. It gets changes before the stable release does."
      : "This is a stable build of Sourcecado.",
    differences: preview ? PREVIEW_DIFFERENCES : STABLE_DIFFERENCES,
  };
}

/** The channel this bundle was built for. Stamped at build time, not read at runtime. */
export function currentChannel(): ChannelDescriptor {
  const env = import.meta.env ?? {};
  return describeChannel(
    env.VITE_SOURCECADO_CHANNEL,
    env.VITE_SOURCECADO_VERSION,
    env.VITE_SOURCECADO_COMMIT,
  );
}

export function isPreview(descriptor: ChannelDescriptor): boolean {
  return descriptor.channel === "preview";
}
