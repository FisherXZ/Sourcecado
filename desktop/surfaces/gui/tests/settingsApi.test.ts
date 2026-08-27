import { afterEach, describe, expect, it, vi } from "vitest";

import { getSettings } from "../src/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("settings provider verification boundary", () => {
  it("rebuilds safe verification fields and drops credential-shaped extras", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          persona: { id: "sourcing", name: "Sourcing agent" },
          model: "sk-settings-PLANTED",
          gmail: { connected: false, email: null },
          apollo: { configured: false },
          providers: [
            {
              provider: "deepseek",
              model: "deepseek-v4-pro",
              selected: true,
              eligible: true,
              failures: [],
              context_window_tokens: 1_000_000,
              capabilities: {
                text: true,
                transient_reasoning: true,
                tool_calling: true,
                terminal_usage: true,
                cache_usage: true,
                reasoning_usage: true,
                token: "provider-secret",
              },
              api_key: "provider-secret",
              raw_error: "private path",
            },
            {
              provider: "unknown-provider",
              model: "private/model",
              selected: false,
              eligible: false,
              failures: ["private failure"],
              capabilities: {},
            },
          ],
          api_key: "settings-secret",
        }),
      }),
    );

    await expect(getSettings()).resolves.toEqual({
      persona: { id: "sourcing", name: "Sourcing agent" },
      model: null,
      gmail: { connected: false, email: null },
      apollo: { configured: false },
      providers: [
        {
          provider: "deepseek",
          model: "deepseek-v4-pro",
          selected: true,
          eligible: true,
          failures: [],
          context_window_tokens: 1_000_000,
          capabilities: {
            text: true,
            transient_reasoning: true,
            tool_calling: true,
            terminal_usage: true,
            cache_usage: true,
            reasoning_usage: true,
          },
        },
      ],
    });
  });
});
