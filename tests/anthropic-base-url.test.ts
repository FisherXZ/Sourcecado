import { resolveAnthropicBaseUrl, toBareAnthropicHost } from "@/lib/model-gateway";

describe("resolveAnthropicBaseUrl", () => {
  it("defaults to the versioned Anthropic API base", () => {
    expect(resolveAnthropicBaseUrl(undefined)).toBe("https://api.anthropic.com/v1");
    expect(resolveAnthropicBaseUrl("  ")).toBe("https://api.anthropic.com/v1");
  });

  it("appends /v1 to a bare host (the official-SDK convention that 404s here)", () => {
    expect(resolveAnthropicBaseUrl("https://api.anthropic.com")).toBe("https://api.anthropic.com/v1");
    expect(resolveAnthropicBaseUrl("https://api.anthropic.com/")).toBe("https://api.anthropic.com/v1");
  });

  it("keeps a base that already has a version segment", () => {
    expect(resolveAnthropicBaseUrl("https://api.anthropic.com/v1")).toBe("https://api.anthropic.com/v1");
    expect(resolveAnthropicBaseUrl("https://proxy.internal/anthropic/v2")).toBe(
      "https://proxy.internal/anthropic/v2",
    );
  });
});

describe("toBareAnthropicHost", () => {
  it("strips a trailing /v1 segment", () => {
    expect(toBareAnthropicHost("https://api.anthropic.com/v1")).toBe("https://api.anthropic.com");
  });

  it("strips a trailing /v2 (or other version) segment", () => {
    expect(toBareAnthropicHost("https://proxy.internal/anthropic/v2")).toBe("https://proxy.internal/anthropic");
  });

  it("is a no-op when there is no version segment", () => {
    expect(toBareAnthropicHost("https://api.anthropic.com")).toBe("https://api.anthropic.com");
  });

  it("composes with resolveAnthropicBaseUrl for the default case", () => {
    expect(toBareAnthropicHost(resolveAnthropicBaseUrl(undefined))).toBe("https://api.anthropic.com");
  });
});
