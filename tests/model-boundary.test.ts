import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const srcRoot = join(process.cwd(), "src");
const allowedProviderFile = join("src", "lib", "model-gateway.ts");

function listSourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      return listSourceFiles(path);
    }
    return path.endsWith(".ts") || path.endsWith(".tsx") ? [path] : [];
  });
}

describe("model provider boundary", () => {
  it("keeps provider SDK imports and direct model URLs inside the Model Gateway", () => {
    const violations: string[] = [];

    for (const file of listSourceFiles(srcRoot)) {
      const relativePath = relative(process.cwd(), file);
      if (relativePath === allowedProviderFile) {
        continue;
      }

      const source = readFileSync(file, "utf8");
      if (source.includes("@ai-sdk/")) {
        violations.push(`${relativePath}: imports provider SDK directly`);
      }
      if (source.includes("https://api.openai.com") || source.includes("api.deepseek.com")) {
        violations.push(`${relativePath}: calls model provider HTTP API directly`);
      }
    }

    expect(violations).toEqual([]);
  });
});
