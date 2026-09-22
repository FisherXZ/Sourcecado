import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  PreviewChannelBadge,
  ReleaseChannelSettings,
} from "../src/routes/UpdateChannel";
import { currentChannel, describeChannel, readChannel } from "../src/updateChannel";

const PREVIEW = describeChannel("preview", "0.0.2", "a1b2c3d4e5f60718293a");
const STABLE = describeChannel("stable", "0.0.1", "0f0e0d0c0b0a09080706");

describe("release channel identity", () => {
  it("reads a preview build as preview", () => {
    expect(readChannel("preview")).toBe("preview");
    expect(PREVIEW.label).toBe("Preview build");
  });

  it("reads anything it does not recognise as stable", () => {
    // A build that cannot say what it is gets the conservative answer, because
    // the stable channel refuses preview updates and the reverse would not.
    expect(readChannel(undefined)).toBe("stable");
    expect(readChannel("")).toBe("stable");
    expect(readChannel("PREVIEW")).toBe("stable");
    expect(readChannel("nightly")).toBe("stable");
    expect(readChannel(null)).toBe("stable");
  });

  it("defaults a developer build with no build stamp to stable", () => {
    expect(currentChannel().channel).toBe("stable");
  });

  it("reads the stamp the packaging workflow writes into the bundle", () => {
    // The one seam between the build and the UI. If the workflow renames a
    // variable, this is what notices.
    vi.stubEnv("VITE_SOURCECADO_CHANNEL", "preview");
    vi.stubEnv("VITE_SOURCECADO_VERSION", "0.0.1.123");
    vi.stubEnv("VITE_SOURCECADO_COMMIT", "fcb6e64aca18c836fa807ee0a009ccd2c9ca3c8a");
    const stamped = currentChannel();
    expect(stamped.channel).toBe("preview");
    expect(stamped.version).toBe("0.0.1.123");
    expect(stamped.commit).toBe("fcb6e64aca18");
  });

  it("shortens the commit and falls back when the build did not stamp one", () => {
    expect(PREVIEW.commit).toBe("a1b2c3d4e5f6");
    expect(describeChannel("preview", "", "").version).toBe("unknown");
  });
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("preview badge", () => {
  it("marks a preview build permanently", () => {
    render(<PreviewChannelBadge descriptor={PREVIEW} />);
    const badge = screen.getByRole("status", { name: "Release channel" });
    expect(badge).toHaveTextContent("Preview build");
    expect(badge).toHaveTextContent("0.0.2");
  });

  it("renders nothing at all on a stable build", () => {
    const { container } = render(<PreviewChannelBadge descriptor={STABLE} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText(/preview/i)).toBeNull();
  });
});

describe("release channel settings", () => {
  it("names the channel, the version, and what is different about it", () => {
    render(<ReleaseChannelSettings descriptor={PREVIEW} />);
    expect(screen.getByRole("heading", { name: "Release channel" })).toBeVisible();
    expect(screen.getByText("Preview build")).toBeVisible();
    expect(screen.getByText("0.0.2")).toBeVisible();
    const behaviour = screen.getByRole("list", { name: "Channel behaviour" });
    expect(behaviour).toHaveTextContent("never updates itself in the background");
    expect(behaviour).toHaveTextContent("backs up your local data");
    expect(behaviour).toHaveTextContent("has not reported back");
  });

  it("tells a preview operator how to get back to a working version", () => {
    render(<ReleaseChannelSettings descriptor={PREVIEW} />);
    expect(screen.getByText("If an update goes wrong")).toBeVisible();
    expect(screen.getByText(/Sourcecado\.app\.previous/)).toBeVisible();
    expect(screen.getByText(/quit Sourcecado/i)).toBeVisible();
  });

  it("offers a stable operator no control that would switch the channel", () => {
    render(<ReleaseChannelSettings descriptor={STABLE} />);
    expect(screen.getByText("Stable")).toBeVisible();
    expect(screen.queryAllByRole("button")).toEqual([]);
    expect(screen.queryAllByRole("checkbox")).toEqual([]);
    expect(screen.queryAllByRole("switch")).toEqual([]);
    const behaviour = screen.getByRole("list", { name: "Channel behaviour" });
    expect(behaviour).toHaveTextContent("install it yourself");
    expect(behaviour).toHaveTextContent("never changes your channel on its own");
  });

  it("does not show the rollback panel on a stable build", () => {
    render(<ReleaseChannelSettings descriptor={STABLE} />);
    expect(screen.queryByText("If an update goes wrong")).toBeNull();
  });
});
