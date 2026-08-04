import { readFileSync } from "node:fs";
import { join } from "node:path";

describe("CI configuration", () => {
  const root = process.cwd();
  const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));

  it("exposes a typecheck script for CI to call", () => {
    expect(pkg.scripts).toHaveProperty("typecheck");
    expect(pkg.scripts.typecheck).toBe("tsc --noEmit");
  });

  it("pins the Node version to 24 (Vercel supports 22.x/24.x, not 26)", () => {
    const nvmrc = readFileSync(join(root, ".nvmrc"), "utf8").trim();
    expect(nvmrc).toBe("24");
  });
});
