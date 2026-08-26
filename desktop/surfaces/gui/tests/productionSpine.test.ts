import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const app = readFileSync("src/App.tsx", "utf8");
const chatPage = readFileSync("src/routes/ChatPage.tsx", "utf8");
const main = readFileSync("src/main.tsx", "utf8");
const styles = readFileSync("src/styles.css", "utf8");
const packageJson = JSON.parse(readFileSync("package.json", "utf8")) as {
  dependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
};

const legacyClassNames = [
  "app",
  "rail",
  "session-list",
  "session-row",
  "main",
  "connector-strip",
  "top",
  "model",
  "transcript",
  "empty",
  "bubble",
  "tool-card",
  "tool-name",
  "tool-result",
  "approval-card",
  "approval-reason",
  "approval-resolved",
  "approval-actions",
  "status",
  "composer",
  "schedule-strip",
  "strip-btn",
] as const;

function exactClassSelector(className: string): RegExp {
  return new RegExp(`(^|[},\\n])\\s*\\.${className}(?=[\\s.{:#>,])`, "m");
}

describe("production UI spine", () => {
  it("keeps App as an AppShell-only bootstrap", () => {
    expect(app).toBe(
      'import { AppShell } from "./app/AppShell";\n\n' +
        "export function App() {\n" +
        "  return <AppShell />;\n" +
        "}\n",
    );
  });

  it("uses the production external-store runtime as ChatPage's only chat owner", () => {
    expect(chatPage).toContain(
      'import { SourcecadoRuntimeProvider } from "../chat/SourcecadoRuntimeProvider";',
    );
    expect(chatPage.match(/<SourcecadoRuntimeProvider\b/g)).toHaveLength(1);
    expect(chatPage).not.toMatch(
      /SourcecadoRuntimeProof|itemsFromMessages|formatArgs|formatResult|\bItem\b/,
    );
    expect(existsSync("src/chat/SourcecadoRuntimeProof.tsx")).toBe(false);
  });

  it("does not keep legacy monolith or utility-strip class owners reachable", () => {
    expect(main).not.toMatch(/className="(?:app|status warn)"/);
    for (const className of legacyClassNames) {
      expect(styles, `legacy .${className} selector`).not.toMatch(
        exactClassSelector(className),
      );
    }
  });

  it("keeps prohibited UI stacks out of direct dependencies", () => {
    const directDependencies = {
      ...packageJson.dependencies,
      ...packageJson.devDependencies,
    };
    for (const dependency of [
      "ai",
      "@ai-sdk/react",
      "assistant-cloud",
      "tailwindcss",
      "@tailwindcss/vite",
      "shadcn",
    ]) {
      expect(directDependencies).not.toHaveProperty(dependency);
    }
    expect(packageJson.dependencies?.react).toMatch(/^\^?18\./);
    expect(packageJson.dependencies?.["react-dom"]).toMatch(/^\^?18\./);
  });
});
