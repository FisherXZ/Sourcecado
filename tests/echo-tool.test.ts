import { getDb } from "@/lib/db";
import { echoTool } from "@/lib/tools/echo";

describe("echoTool", () => {
  it("echoes the provided text", async () => {
    const result = await echoTool.execute(
      { text: "hello" },
      { db: getDb(), runId: 0, parentStepId: 0 },
    );
    expect(result).toEqual({ echoed: "hello" });
  });

  it("is a read-class tool named echo", () => {
    expect(echoTool.name).toBe("echo");
    expect(echoTool.permissionClass).toBe("read");
  });
});
