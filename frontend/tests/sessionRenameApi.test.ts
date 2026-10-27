import { beforeEach, describe, expect, it, vi } from "vitest";

import { renameSession } from "../src/api";

describe("session rename API boundary", () => {
  beforeEach(() => {
    window.__CLUB_HTTP__ = "http://backend.test";
    window.__CLUB_API_TOKEN__ = "review-token";
  });

  it("announces a successful rename so the open thread's header can follow it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ id: "thread one", title: "Renamed", pinned: false }),
      }),
    );
    const heard: unknown[] = [];
    const listener = (event: Event) => heard.push((event as CustomEvent).detail);
    window.addEventListener("sourcecado:session-renamed", listener);

    await renameSession("thread one", "Renamed");

    window.removeEventListener("sourcecado:session-renamed", listener);
    expect(heard).toEqual([{ id: "thread one", title: "Renamed", pinned: false }]);
  });

  it("announces nothing when the rename fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 }));
    const heard: unknown[] = [];
    const listener = (event: Event) => heard.push((event as CustomEvent).detail);
    window.addEventListener("sourcecado:session-renamed", listener);

    await expect(renameSession("thread one", "Renamed")).rejects.toThrow("rename 500");

    window.removeEventListener("sourcecado:session-renamed", listener);
    expect(heard).toEqual([]);
  });
});
