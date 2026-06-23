import { isRunInspectorEnabled } from "@/lib/run-inspector-access";

describe("run inspector access", () => {
  it("is disabled by default", () => {
    expect(isRunInspectorEnabled({})).toBe(false);
  });

  it("can be enabled for local development", () => {
    expect(
      isRunInspectorEnabled({
        NODE_ENV: "development",
        SOURCECADO_ENABLE_RUN_INSPECTOR: "true",
      }),
    ).toBe(true);
  });

  it("stays disabled in production even when configured", () => {
    expect(
      isRunInspectorEnabled({
        NODE_ENV: "production",
        SOURCECADO_ENABLE_RUN_INSPECTOR: "true",
      }),
    ).toBe(false);
  });
});
