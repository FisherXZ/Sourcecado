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
          model: "xoxb-PLANTED-SENTINEL",
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
              provider: "openai",
              model: "xoxb-PLANTED-SENTINEL",
              selected: true,
              eligible: true,
              failures: [],
              context_window_tokens: null,
              capabilities: {
                text: true,
                transient_reasoning: true,
                tool_calling: true,
                terminal_usage: true,
                cache_usage: true,
                reasoning_usage: true,
              },
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
        {
          provider: "openai",
          model: null,
          selected: false,
          eligible: false,
          failures: [],
          context_window_tokens: null,
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

  it.each([
    "xoxb-PLANTED-SENTINEL",
    "xoxp-PLANTED-SENTINEL",
    "xapp-PLANTED-SENTINEL",
    "C:/Users/operator/model",
    "file:/private/model",
    "https://provider.example/model",
    "org/../model",
    "org/./model",
    "org//model",
    "model with spaces",
    "model\nwith-control",
  ])("rejects unsafe model shape %s symmetrically", async (unsafeModel) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          persona: { id: "sourcing", name: "Sourcing agent" },
          model: unsafeModel,
          gmail: { connected: false, email: null },
          apollo: { configured: false },
          providers: [
            {
              provider: "openai",
              model: unsafeModel,
              selected: true,
              eligible: true,
              failures: [],
              context_window_tokens: null,
              capabilities: {},
            },
          ],
        }),
      }),
    );

    const settings = await getSettings();

    expect(settings.model).toBeNull();
    expect(settings.providers[0]).toMatchObject({
      model: null,
      selected: false,
      eligible: false,
    });
    expect(JSON.stringify(settings)).not.toContain(unsafeModel);
  });

  it("preserves a safe custom OpenAI-compatible model identifier", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          persona: { id: "sourcing", name: "Sourcing agent" },
          model: "vendor/custom-model:v2",
          gmail: { connected: false, email: null },
          apollo: { configured: false },
          providers: [
            {
              provider: "openai",
              model: "vendor/custom-model:v2",
              selected: true,
              eligible: true,
              failures: [],
              context_window_tokens: null,
              capabilities: {},
            },
          ],
        }),
      }),
    );

    const settings = await getSettings();

    expect(settings.model).toBe("vendor/custom-model:v2");
    expect(settings.providers[0]).toMatchObject({
      provider: "openai",
      model: "vendor/custom-model:v2",
      selected: true,
      eligible: true,
    });
  });
});
