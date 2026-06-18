# Design Foundation Retrofit (FD) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the "Warm Operator" design system (DESIGN.md) into the Next.js app — tokens, fonts, a reusable primitive kit, and a retrofit of the F1 shell — so every downstream UI slice is built on-theme instead of restyled later.

**Architecture:** Tailwind v4 `@theme` tokens backed by CSS variables that flip on `[data-theme]` (single source of truth for color/type/radius). A small set of presentational React primitives in `src/components/ui/` reference only token utilities, never raw hex. The existing F1 pages are rebuilt on a three-pane `AppShell`. A `/styleguide` route renders every primitive and doubles as the visual-QA surface.

**Tech Stack:** Next.js 15 (App Router), React 19, Tailwind CSS v4, TypeScript, Vitest. Fonts: General Sans (self-hosted via `next/font/local`) + Geist Mono (`geist` npm package). Component tests: `@testing-library/react` + `jsdom` + `@vitejs/plugin-react`.

## Global Constraints

- **Source of truth:** `DESIGN.md` at repo root. All colors, fonts, spacing, radius, and motion values come from it verbatim. Do not invent values.
- **No raw hex in components.** `src/components/ui/**` and `src/app/**/*.tsx` reference token utilities (`bg-canvas`, `text-muted`, `bg-accent`, etc.) only. Raw hex is allowed solely in `globals.css` (token definitions).
- **Fonts:** General Sans for UI/body, Geist Mono for IDs/numbers/data. Never Inter/Geist Sans/system as the primary UI font.
- **Density:** body 13px in dense surfaces, table rows 36–38px, 4px spacing base. Radius: 6px buttons, 8px cards/inputs/panels, full on pills/avatars.
- **Naming:** product is **Sourcecado**. Rename is scoped to user-facing UI (`src/app/**`, `src/components/**`) only. Do NOT touch `CONTEXT.md`, `docs/**`, `CHANGELOG.md`, or `src/extractors/llm.ts` in this plan — the broader "SourcyAvo" cleanup is a separate decision.
- **Backend untouched:** no changes to `src/db.ts`, migrations, `src/answer.ts`, `src/ingest.ts`, etc. The existing Vitest suite must stay green.
- **Tailwind v4:** tokens via `@theme inline` in `globals.css`; no `tailwind.config.js` color edits (v4 is CSS-first).

---

### Task 1: Design tokens + fonts wiring

Establish the token layer and load the two typefaces. This is the foundation every other task builds on.

**Files:**
- Modify: `src/app/globals.css` (replace entirely)
- Modify: `src/app/layout.tsx`
- Create: `src/app/fonts/GeneralSans-Variable.woff2` (downloaded asset)
- Modify: `package.json` (add `geist` dependency)

**Interfaces:**
- Produces: token utility classes (`bg-canvas`, `bg-surface`, `bg-raised`, `text-text`, `text-muted`, `border-border`, `bg-accent`, `text-accent`, `bg-accent-tint`, `text-accent-deep`, `bg-pit`, `text-pit`, `bg-pit-tint`, `bg-warn-bg`/`text-warn-tx`, `bg-neg-bg`/`text-neg-tx`), `font-sans` (General Sans), `font-mono` (Geist Mono). All flip under `[data-theme="dark"]`.

- [ ] **Step 1: Install Geist font package**

Run:
```bash
npm install geist
```
Expected: `geist` added to `dependencies` in `package.json`.

- [ ] **Step 2: Download the General Sans variable font**

Download the variable woff2 from Fontshare into the fonts dir:
```bash
mkdir -p src/app/fonts
curl -fsSL "https://fontshare-cdn.b-cdn.net/fonts/general-sans/fonts/GeneralSans-Variable.woff2" -o src/app/fonts/GeneralSans-Variable.woff2
ls -la src/app/fonts/GeneralSans-Variable.woff2
```
Expected: a non-empty `.woff2` file (~60–120KB). If that URL 404s, download `GeneralSans-Variable.woff2` from https://www.fontshare.com/fonts/general-sans (Download family → extract the `Fonts/variable/` woff2) and place it at the same path. Verify the file is > 10KB before continuing.

- [ ] **Step 3: Replace `globals.css` with the token layer**

Replace the entire contents of `src/app/globals.css`:
```css
@import "tailwindcss";

:root {
  --canvas: #FAF8F3;
  --surface: #FEFDFB;
  --raised: #F5F2EA;
  --text: #2B2722;
  --muted: #78716C;
  --border: #E7E3DA;
  --accent: #5B8C2A;
  --accent-tint: #EBF1DF;
  --accent-deep: #3E5E1C;
  --pit: #C2703D;
  --pit-tint: #F7EADF;
  --warn-bg: #FBEFD8;
  --warn-tx: #92591B;
  --neg-bg: #FBE6E1;
  --neg-tx: #9B3B2E;
}

[data-theme="dark"] {
  --canvas: #1A1815;
  --surface: #232019;
  --raised: #2C2821;
  --text: #F0EBE2;
  --muted: #A8A096;
  --border: #3A352D;
  --accent: #8FBF5C;
  --accent-tint: #2C3320;
  --accent-deep: #B7DC8C;
  --pit: #D98A57;
  --pit-tint: #36281D;
  --warn-bg: #3A2E16;
  --warn-tx: #E0B062;
  --neg-bg: #3A211B;
  --neg-tx: #E08A77;
}

@theme inline {
  --color-canvas: var(--canvas);
  --color-surface: var(--surface);
  --color-raised: var(--raised);
  --color-text: var(--text);
  --color-muted: var(--muted);
  --color-border: var(--border);
  --color-accent: var(--accent);
  --color-accent-tint: var(--accent-tint);
  --color-accent-deep: var(--accent-deep);
  --color-pit: var(--pit);
  --color-pit-tint: var(--pit-tint);
  --color-warn-bg: var(--warn-bg);
  --color-warn-tx: var(--warn-tx);
  --color-neg-bg: var(--neg-bg);
  --color-neg-tx: var(--neg-tx);
  --font-sans: var(--font-general-sans), ui-sans-serif, system-ui, sans-serif;
  --font-mono: var(--font-geist-mono), ui-monospace, monospace;
}

body {
  background: var(--canvas);
  color: var(--text);
  font-family: var(--font-sans);
  -webkit-font-smoothing: antialiased;
}
```

- [ ] **Step 4: Wire the fonts in `layout.tsx`**

Replace `src/app/layout.tsx` (the `<AppShell>` arrives in Task 6 — for now keep the body simple so the app still builds):
```tsx
import type { Metadata } from "next";
import localFont from "next/font/local";
import { GeistMono } from "geist/font/mono";
import "./globals.css";

const generalSans = localFont({
  src: "./fonts/GeneralSans-Variable.woff2",
  variable: "--font-general-sans",
  weight: "400 700",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Sourcecado",
  description: "Hosted team sourcing operating system for Codeology",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      data-theme="light"
      className={`${generalSans.variable} ${GeistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full bg-canvas text-text font-sans">{children}</body>
    </html>
  );
}
```

- [ ] **Step 5: Verify the build compiles with tokens and fonts**

Run:
```bash
npm run build
```
Expected: build succeeds. Then confirm tokens and fonts are present:
```bash
grep -c "color-accent" src/app/globals.css   # expect >= 1
grep -c "GeistMono" src/app/layout.tsx        # expect 1
```

- [ ] **Step 6: Commit**

```bash
git add src/app/globals.css src/app/layout.tsx src/app/fonts/GeneralSans-Variable.woff2 package.json package-lock.json
git commit -m "feat(fd): wire Warm Operator design tokens + General Sans/Geist Mono fonts"
```

---

### Task 2: UI test infrastructure + Button + StatusPill

Stand up React component testing, then prove it with the two simplest variant-driven primitives.

**Files:**
- Modify: `vitest.config.ts`
- Modify: `tsconfig.test.json`
- Modify: `package.json` (dev deps)
- Create: `src/components/ui/Button.tsx`
- Create: `src/components/ui/StatusPill.tsx`
- Create: `src/components/ui/index.ts`
- Test: `tests/components/Button.test.tsx`
- Test: `tests/components/StatusPill.test.tsx`

**Interfaces:**
- Produces:
  - `Button({ variant?: "primary" | "ghost"; children; ...buttonProps })` — default `variant="primary"`.
  - `StatusPill({ tone: "go" | "draft" | "reply" | "warn" | "error"; children })`.
  - `Tag({ children })` — quiet outlined label.
  - `src/components/ui/index.ts` re-exports every primitive (barrel for `@/components/ui`).

- [ ] **Step 1: Install component-test dependencies**

Run:
```bash
npm install -D @testing-library/react @testing-library/jest-dom jsdom @vitejs/plugin-react
```
Expected: four packages added to `devDependencies`.

- [ ] **Step 2: Configure Vitest for React + per-file jsdom**

Replace `vitest.config.ts`:
```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  test: {
    environment: "node",
    globals: true,
    tsconfig: "./tsconfig.test.json",
  },
});
```
(Global env stays `node` so backend tests are unchanged; component test files opt into jsdom with a `// @vitest-environment jsdom` annotation.)

- [ ] **Step 3: Allow `.tsx` test files in the test tsconfig**

In `tsconfig.test.json`, change the `include` array to add tsx files:
```json
{
  "extends": "./tsconfig.json",
  "compilerOptions": {
    "types": ["node", "vitest/globals"]
  },
  "include": ["tests/**/*.ts", "tests/**/*.tsx", "vitest.config.ts"]
}
```

- [ ] **Step 4: Write the failing Button test**

Create `tests/components/Button.test.tsx`:
```tsx
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { Button } from "@/components/ui";

describe("Button", () => {
  it("renders a primary button with the accent token by default", () => {
    render(<Button>Create draft</Button>);
    const btn = screen.getByRole("button", { name: "Create draft" });
    expect(btn).toBeInTheDocument();
    expect(btn.className).toContain("bg-accent");
  });

  it("renders a ghost variant with a border token, not the accent fill", () => {
    render(<Button variant="ghost">Cancel</Button>);
    const btn = screen.getByRole("button", { name: "Cancel" });
    expect(btn.className).toContain("border-border");
    expect(btn.className).not.toContain("bg-accent");
  });
});
```

- [ ] **Step 5: Run the test to verify it fails**

Run:
```bash
npx vitest run tests/components/Button.test.tsx
```
Expected: FAIL — cannot resolve `@/components/ui` (module not created yet).

- [ ] **Step 6: Implement Button, StatusPill, Tag, and the barrel**

Create `src/components/ui/Button.tsx`:
```tsx
import type { ButtonHTMLAttributes, ReactNode } from "react";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost";
  children: ReactNode;
};

const base =
  "inline-flex items-center gap-2 rounded-[6px] px-3 py-1.5 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-accent-tint disabled:opacity-50 disabled:pointer-events-none";
const variants = {
  primary: "bg-accent text-white hover:bg-accent-deep",
  ghost: "bg-transparent border border-border text-text hover:border-accent hover:text-accent-deep",
};

export function Button({ variant = "primary", children, className = "", ...rest }: Props) {
  return (
    <button className={`${base} ${variants[variant]} ${className}`} {...rest}>
      {children}
    </button>
  );
}
```

Create `src/components/ui/StatusPill.tsx`:
```tsx
import type { ReactNode } from "react";

type Tone = "go" | "draft" | "reply" | "warn" | "error";

const tones: Record<Tone, string> = {
  go: "bg-accent-tint text-accent-deep",
  draft: "bg-warn-bg text-warn-tx",
  reply: "bg-pit-tint text-pit",
  warn: "bg-warn-bg text-warn-tx",
  error: "bg-neg-bg text-neg-tx",
};

export function StatusPill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11.5px] font-medium ${tones[tone]}`}
    >
      <span className="h-[5px] w-[5px] rounded-full bg-current opacity-70" />
      {children}
    </span>
  );
}

export function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="mr-1 rounded-[4px] border border-border px-1.5 py-0.5 text-[11px] text-muted">
      {children}
    </span>
  );
}
```

Create `src/components/ui/index.ts`:
```ts
export { Button } from "./Button";
export { StatusPill, Tag } from "./StatusPill";
```

- [ ] **Step 7: Run the Button test to verify it passes**

Run:
```bash
npx vitest run tests/components/Button.test.tsx
```
Expected: PASS (2 tests).

- [ ] **Step 8: Write and run the StatusPill test**

Create `tests/components/StatusPill.test.tsx`:
```tsx
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { StatusPill } from "@/components/ui";

describe("StatusPill", () => {
  it("maps the 'go' tone to the accent-tint token", () => {
    render(<StatusPill tone="go">Sourced</StatusPill>);
    const pill = screen.getByText("Sourced");
    expect(pill.className).toContain("bg-accent-tint");
    expect(pill.className).toContain("text-accent-deep");
  });

  it("maps the 'error' tone to the negative tokens", () => {
    render(<StatusPill tone="error">Failed</StatusPill>);
    expect(screen.getByText("Failed").className).toContain("bg-neg-bg");
  });
});
```
Run:
```bash
npx vitest run tests/components/StatusPill.test.tsx
```
Expected: PASS (2 tests).

- [ ] **Step 9: Confirm the existing backend suite is unaffected**

Run:
```bash
npm test
```
Expected: all pre-existing tests still pass alongside the 2 new component test files.

- [ ] **Step 10: Commit**

```bash
git add vitest.config.ts tsconfig.test.json package.json package-lock.json src/components/ui tests/components
git commit -m "feat(fd): add UI test infra + Button/StatusPill primitives"
```

---

### Task 3: Input, Toggle, Card, EmptyState

The remaining simple primitives, including the warm-zone `EmptyState`.

**Files:**
- Create: `src/components/ui/Input.tsx`
- Create: `src/components/ui/Card.tsx`
- Create: `src/components/ui/AvocadoMark.tsx`
- Create: `src/components/ui/EmptyState.tsx`
- Modify: `src/components/ui/index.ts`
- Test: `tests/components/Input.test.tsx`
- Test: `tests/components/EmptyState.test.tsx`

**Interfaces:**
- Produces:
  - `Input(inputProps)` — text input with token styling + accent focus ring.
  - `Toggle({ on: boolean; onToggle?: () => void; label?: string })`.
  - `Card({ children; className? })` — surface panel, 8px radius, hairline border.
  - `EmptyState({ title; description; action?: ReactNode })` — centered warm-zone block with the avocado mark.

- [ ] **Step 1: Write the failing Input + EmptyState tests**

Create `tests/components/Input.test.tsx`:
```tsx
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Toggle } from "@/components/ui";

describe("Toggle", () => {
  it("fires onToggle when clicked", () => {
    let count = 0;
    render(<Toggle on={false} onToggle={() => (count += 1)} label="Auto-enrich" />);
    fireEvent.click(screen.getByRole("switch"));
    expect(count).toBe(1);
  });

  it("reflects the on state via aria-checked", () => {
    render(<Toggle on={true} label="Auto-enrich" />);
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true");
  });
});
```

Create `tests/components/EmptyState.test.tsx`:
```tsx
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { EmptyState, Button } from "@/components/ui";

describe("EmptyState", () => {
  it("renders title, description, and an optional action", () => {
    render(
      <EmptyState
        title="No contacts yet"
        description="Run a routine to pull candidates."
        action={<Button>Run routine</Button>}
      />,
    );
    expect(screen.getByText("No contacts yet")).toBeInTheDocument();
    expect(screen.getByText("Run a routine to pull candidates.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run routine" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
npx vitest run tests/components/Input.test.tsx tests/components/EmptyState.test.tsx
```
Expected: FAIL — `Toggle` / `EmptyState` not exported.

- [ ] **Step 3: Implement the primitives**

Create `src/components/ui/Input.tsx`:
```tsx
import type { InputHTMLAttributes } from "react";

export function Input({ className = "", ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={`w-full rounded-[6px] border border-border bg-canvas px-3 py-2 text-[13px] text-text outline-none placeholder:text-muted focus:border-accent focus:ring-[3px] focus:ring-accent-tint ${className}`}
      {...rest}
    />
  );
}

export function Toggle({
  on,
  onToggle,
  label,
}: {
  on: boolean;
  onToggle?: () => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={onToggle}
      className={`relative inline-block h-[22px] w-[38px] rounded-full transition-colors ${
        on ? "bg-accent" : "bg-border"
      }`}
    >
      <span
        className={`absolute top-[2px] h-[18px] w-[18px] rounded-full bg-white transition-all ${
          on ? "left-[18px]" : "left-[2px]"
        }`}
      />
    </button>
  );
}
```

Create `src/components/ui/Card.tsx`:
```tsx
import type { ReactNode } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-[8px] border border-border bg-surface p-4 ${className}`}>
      {children}
    </div>
  );
}
```

Create `src/components/ui/AvocadoMark.tsx` (shared by EmptyState and AppShell — its own file to avoid a cross-component import):
```tsx
export function AvocadoMark({ size = 56 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <ellipse cx="12" cy="13" rx="7.5" ry="9.5" fill="var(--accent)" />
      <ellipse cx="12" cy="14.5" rx="3.3" ry="3.8" fill="var(--pit)" />
      <circle cx="9.6" cy="9" r="0.9" fill="#fff" opacity="0.85" />
    </svg>
  );
}
```

Create `src/components/ui/EmptyState.tsx`:
```tsx
import type { ReactNode } from "react";
import { AvocadoMark } from "./AvocadoMark";

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-[560px] rounded-[12px] border border-dashed border-border bg-raised p-11 text-center">
      <div className="flex justify-center">
        <AvocadoMark />
      </div>
      <h3 className="mb-1.5 mt-4 text-[18px] font-semibold">{title}</h3>
      <p className="mb-4 text-[14px] text-muted">{description}</p>
      {action && <div className="flex justify-center">{action}</div>}
    </div>
  );
}
```

Update `src/components/ui/index.ts` to add the new exports:
```ts
export { Button } from "./Button";
export { StatusPill, Tag } from "./StatusPill";
export { Input, Toggle } from "./Input";
export { Card } from "./Card";
export { AvocadoMark } from "./AvocadoMark";
export { EmptyState } from "./EmptyState";
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
npx vitest run tests/components/Input.test.tsx tests/components/EmptyState.test.tsx
```
Expected: PASS (3 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/components/ui tests/components
git commit -m "feat(fd): add Input/Toggle/Card/EmptyState primitives"
```

---

### Task 4: DataTable

The hero object: a generic, dense table with sticky header, tabular numerals, sortable headers, and row selection.

**Files:**
- Create: `src/components/ui/DataTable.tsx`
- Modify: `src/components/ui/index.ts`
- Test: `tests/components/DataTable.test.tsx`

**Interfaces:**
- Produces:
  ```ts
  type Column<T> = {
    key: string;
    header: string;
    numeric?: boolean;            // applies tabular-nums + right align
    sortable?: boolean;
    render?: (row: T) => ReactNode;
  };
  function DataTable<T>(props: {
    columns: Column<T>[];
    rows: T[];
    getRowId: (row: T) => string;
    selectedIds?: Set<string>;
    onToggleRow?: (id: string) => void;
    onSort?: (key: string) => void;
  }): JSX.Element;
  ```
- Consumes: token utilities from Task 1.

- [ ] **Step 1: Write the failing DataTable test**

Create `tests/components/DataTable.test.tsx`:
```tsx
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DataTable } from "@/components/ui";

type Row = { id: string; name: string; fit: number };
const rows: Row[] = [
  { id: "1", name: "Maya Rao", fit: 94 },
  { id: "2", name: "Jordan Kim", fit: 88 },
];
const columns = [
  { key: "name", header: "Name", sortable: true },
  { key: "fit", header: "Fit", numeric: true },
];

describe("DataTable", () => {
  it("renders headers and a row per item", () => {
    render(<DataTable columns={columns} rows={rows} getRowId={(r) => r.id} />);
    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(screen.getByText("Maya Rao")).toBeInTheDocument();
    expect(screen.getByText("Jordan Kim")).toBeInTheDocument();
  });

  it("applies tabular-nums to numeric cells", () => {
    render(<DataTable columns={columns} rows={rows} getRowId={(r) => r.id} />);
    expect(screen.getByText("94").className).toContain("tabular-nums");
  });

  it("calls onSort when a sortable header is clicked", () => {
    let sorted = "";
    render(
      <DataTable columns={columns} rows={rows} getRowId={(r) => r.id} onSort={(k) => (sorted = k)} />,
    );
    fireEvent.click(screen.getByText("Name"));
    expect(sorted).toBe("name");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
npx vitest run tests/components/DataTable.test.tsx
```
Expected: FAIL — `DataTable` not exported.

- [ ] **Step 3: Implement DataTable**

Create `src/components/ui/DataTable.tsx`:
```tsx
import type { ReactNode } from "react";

export type Column<T> = {
  key: string;
  header: string;
  numeric?: boolean;
  sortable?: boolean;
  render?: (row: T) => ReactNode;
};

export function DataTable<T>({
  columns,
  rows,
  getRowId,
  selectedIds,
  onToggleRow,
  onSort,
}: {
  columns: Column<T>[];
  rows: T[];
  getRowId: (row: T) => string;
  selectedIds?: Set<string>;
  onToggleRow?: (id: string) => void;
  onSort?: (key: string) => void;
}) {
  return (
    <div className="flex-1 overflow-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr>
            {onToggleRow && <th className="sticky top-0 w-[34px] bg-surface" />}
            {columns.map((col) => (
              <th
                key={col.key}
                onClick={col.sortable && onSort ? () => onSort(col.key) : undefined}
                className={`sticky top-0 whitespace-nowrap border-b border-border bg-surface px-3.5 py-2.5 text-[11px] font-medium uppercase tracking-wide text-muted ${
                  col.numeric ? "text-right" : "text-left"
                } ${col.sortable ? "cursor-pointer hover:text-accent-deep" : ""}`}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const id = getRowId(row);
            const selected = selectedIds?.has(id);
            return (
              <tr
                key={id}
                onClick={onToggleRow ? () => onToggleRow(id) : undefined}
                className={`cursor-pointer border-b border-border ${
                  selected ? "bg-accent-tint" : "hover:bg-raised"
                }`}
              >
                {onToggleRow && (
                  <td className="px-3.5">
                    <span
                      className={`inline-block h-[15px] w-[15px] rounded-[4px] border ${
                        selected ? "border-accent bg-accent" : "border-border"
                      }`}
                    />
                  </td>
                )}
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className={`h-[38px] whitespace-nowrap px-3.5 ${
                      col.numeric ? "text-right font-mono tabular-nums" : ""
                    }`}
                  >
                    {col.render ? col.render(row) : String((row as Record<string, unknown>)[col.key])}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
```

Add to `src/components/ui/index.ts`:
```ts
export { DataTable } from "./DataTable";
export type { Column } from "./DataTable";
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
npx vitest run tests/components/DataTable.test.tsx
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/components/ui tests/components
git commit -m "feat(fd): add DataTable primitive (sticky header, tabular nums, sort, select)"
```

---

### Task 5: AppShell

The three-pane application shell with a config-driven nav.

**Files:**
- Create: `src/components/ui/AppShell.tsx`
- Modify: `src/components/ui/index.ts`
- Test: `tests/components/AppShell.test.tsx`

**Interfaces:**
- Produces:
  ```ts
  type NavItem = { label: string; href: string; icon?: ReactNode };
  function AppShell(props: {
    nav: NavItem[];
    activeHref?: string;
    user?: { name: string; role: string };
    inspector?: ReactNode;     // optional right pane
    children: ReactNode;       // center pane
  }): JSX.Element;
  ```
- Uses Next.js `Link` for nav items.

- [ ] **Step 1: Write the failing AppShell test**

Create `tests/components/AppShell.test.tsx`:
```tsx
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { AppShell } from "@/components/ui";

describe("AppShell", () => {
  const nav = [
    { label: "Research Chat", href: "/chat" },
    { label: "Run Ledger", href: "/runs" },
  ];

  it("renders the brand, nav items, and the center children", () => {
    render(
      <AppShell nav={nav} activeHref="/chat">
        <div>center content</div>
      </AppShell>,
    );
    expect(screen.getByText("Sourcecado")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Research Chat" })).toHaveAttribute("href", "/chat");
    expect(screen.getByText("center content")).toBeInTheDocument();
  });

  it("marks the active nav item with the accent-tint token", () => {
    render(
      <AppShell nav={nav} activeHref="/chat">
        <div />
      </AppShell>,
    );
    expect(screen.getByRole("link", { name: "Research Chat" }).className).toContain("bg-accent-tint");
  });

  it("renders the inspector pane only when provided", () => {
    const { rerender } = render(
      <AppShell nav={nav}>
        <div />
      </AppShell>,
    );
    expect(screen.queryByTestId("inspector")).not.toBeInTheDocument();
    rerender(
      <AppShell nav={nav} inspector={<div>panel</div>}>
        <div />
      </AppShell>,
    );
    expect(screen.getByTestId("inspector")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
npx vitest run tests/components/AppShell.test.tsx
```
Expected: FAIL — `AppShell` not exported.

- [ ] **Step 3: Implement AppShell**

Create `src/components/ui/AppShell.tsx`:
```tsx
import type { ReactNode } from "react";
import Link from "next/link";
import { AvocadoMark } from "./AvocadoMark";

export type NavItem = { label: string; href: string; icon?: ReactNode };

export function AppShell({
  nav,
  activeHref,
  user,
  inspector,
  children,
}: {
  nav: NavItem[];
  activeHref?: string;
  user?: { name: string; role: string };
  inspector?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div
      className={`grid min-h-screen grid-cols-1 ${
        inspector ? "md:grid-cols-[232px_1fr_300px]" : "md:grid-cols-[232px_1fr]"
      }`}
    >
      {/* Mobile top bar: keeps branding present when the nav rail is hidden.
          A full mobile nav drawer is intentionally deferred (desktop-first tool). */}
      <header className="flex items-center gap-2 border-b border-border bg-raised px-4 py-3 md:hidden">
        <AvocadoMark size={20} />
        <span className="font-semibold tracking-tight">Sourcecado</span>
      </header>

      <aside className="hidden flex-col border-r border-border bg-raised p-3 md:flex">
        <Link
          href="/"
          className="mb-4 flex items-center gap-2.5 rounded-[7px] px-2 py-1 font-semibold tracking-tight focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-tint"
        >
          <AvocadoMark size={22} />
          Sourcecado
        </Link>
        <nav className="flex flex-col gap-0.5">
          {nav.map((item) => {
            const active = item.href === activeHref;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`flex items-center gap-2.5 rounded-[7px] px-2.5 py-1.5 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-tint ${
                  active
                    ? "bg-accent-tint font-medium text-accent-deep"
                    : "text-text hover:bg-surface"
                }`}
              >
                {item.icon && <span className="flex h-4 w-4 items-center justify-center">{item.icon}</span>}
                {item.label}
              </Link>
            );
          })}
        </nav>
        {user && (
          <div className="mt-auto flex items-center gap-2.5 border-t border-border pt-3">
            <span className="grid h-[26px] w-[26px] place-items-center rounded-full bg-pit text-[11px] font-semibold text-white">
              {user.name.slice(0, 2).toUpperCase()}
            </span>
            <div>
              <div className="text-[12.5px] font-medium">{user.name}</div>
              <div className="text-[11px] text-muted">{user.role}</div>
            </div>
          </div>
        )}
      </aside>

      <main className="min-w-0 bg-canvas">{children}</main>

      {inspector && (
        <aside data-testid="inspector" className="hidden border-l border-border bg-surface p-4 md:block">
          {inspector}
        </aside>
      )}
    </div>
  );
}
```

Add to `src/components/ui/index.ts`:
```ts
export { AppShell } from "./AppShell";
export type { NavItem } from "./AppShell";
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
npx vitest run tests/components/AppShell.test.tsx
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/components/ui tests/components
git commit -m "feat(fd): add three-pane AppShell with config-driven nav"
```

---

### Task 6: F1 retrofit — shell, rename, home + /chat

Put the kit to work: render the app inside `AppShell`, rename the UI to Sourcecado, and rebuild the two placeholder pages.

**Files:**
- Modify: `src/app/layout.tsx`
- Modify: `src/app/page.tsx`
- Modify: `src/app/chat/page.tsx`
- Create: `src/lib/nav.ts`

**Interfaces:**
- Consumes: `AppShell`, `NavItem`, `Button`, `EmptyState` from `@/components/ui`.
- Produces: `NAV` (shared nav config) from `@/lib/nav`.

- [ ] **Step 1: Create the shared nav config (live routes only)**

Create `src/lib/nav.ts`:
```ts
import type { NavItem } from "@/components/ui";

// Live routes only. Downstream slices append their entry here as pages land
// (e.g. Contacts, Run Ledger, Drafts, Memory, Routines).
export const NAV: NavItem[] = [{ label: "Research Chat", href: "/chat" }];
```

- [ ] **Step 2: Render the app inside AppShell**

Replace the body of `src/app/layout.tsx` to wrap children in `AppShell` (keep the font wiring from Task 1):
```tsx
import type { Metadata } from "next";
import localFont from "next/font/local";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import { AppShell } from "@/components/ui";
import { NAV } from "@/lib/nav";

const generalSans = localFont({
  src: "./fonts/GeneralSans-Variable.woff2",
  variable: "--font-general-sans",
  weight: "400 700",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Sourcecado",
  description: "Hosted team sourcing operating system for Codeology",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      data-theme="light"
      className={`${generalSans.variable} ${GeistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full bg-canvas text-text font-sans">
        <AppShell nav={NAV} user={{ name: "Sourcing Director", role: "Codeology" }}>
          {children}
        </AppShell>
      </body>
    </html>
  );
}
```

- [ ] **Step 3: Rebuild the home page**

Replace `src/app/page.tsx`:
```tsx
import Link from "next/link";
import { Button } from "@/components/ui";

export default function Home() {
  return (
    <div className="mx-auto flex min-h-[60vh] max-w-[640px] flex-col items-center justify-center gap-4 px-6 text-center">
      <h1 className="text-[32px] font-semibold tracking-tight">Sourcecado</h1>
      <p className="max-w-sm text-muted">
        Hosted sourcing operating system for Codeology. Ask questions about contacts,
        outreach history, and sourcing context — with cited answers.
      </p>
      <Link href="/chat">
        <Button>Open Research Chat</Button>
      </Link>
    </div>
  );
}
```

- [ ] **Step 4: Rebuild the /chat placeholder with EmptyState**

Replace `src/app/chat/page.tsx`:
```tsx
import { EmptyState } from "@/components/ui";

export default function ChatPage() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center px-6">
      <EmptyState
        title="Research Chat is coming soon"
        description="Ask sourcing questions here and get cited answers with knowledge gaps. The agent run lands in Feature A."
      />
    </div>
  );
}
```

- [ ] **Step 5: Verify the UI rename is complete (scoped to UI dirs)**

Run:
```bash
grep -rn "SourcyAvo" src/app src/components 2>/dev/null || echo "CLEAN: no SourcyAvo in UI"
```
Expected: `CLEAN: no SourcyAvo in UI`. (Occurrences in `docs/`, `CONTEXT.md`, `CHANGELOG.md`, and `src/extractors/llm.ts` are intentionally out of scope.)

- [ ] **Step 6: Verify the build and that pages render the shell**

Run:
```bash
npm run build
```
Expected: build succeeds with `/`, `/chat`, and `/api/health` in the route list.

- [ ] **Step 7: Commit**

```bash
git add src/app/layout.tsx src/app/page.tsx src/app/chat/page.tsx src/lib/nav.ts
git commit -m "feat(fd): retrofit F1 shell onto AppShell + rename UI to Sourcecado"
```

---

### Task 7: /styleguide catalog page

A single page that renders every primitive in both themes — the visual-QA surface and living documentation.

**Files:**
- Create: `src/app/styleguide/page.tsx`

**Interfaces:**
- Consumes: every export from `@/components/ui`.

- [ ] **Step 1: Build the styleguide page with a theme toggle**

Create `src/app/styleguide/page.tsx`:
```tsx
"use client";
import { useState } from "react";
import {
  AppShell,
  Button,
  StatusPill,
  Tag,
  Input,
  Toggle,
  Card,
  EmptyState,
  DataTable,
  type Column,
} from "@/components/ui";

type Row = { id: string; name: string; company: string; fit: number; status: "go" | "draft" | "reply" };
const rows: Row[] = [
  { id: "1", name: "Maya Rao", company: "Stripe", fit: 94, status: "go" },
  { id: "2", name: "Jordan Kim", company: "Notion", fit: 88, status: "draft" },
  { id: "3", name: "Amara Diallo", company: "Vercel", fit: 86, status: "reply" },
];
const columns: Column<Row>[] = [
  { key: "name", header: "Name", sortable: true },
  { key: "company", header: "Company" },
  { key: "fit", header: "Fit", numeric: true },
  { key: "status", header: "Status", render: (r) => <StatusPill tone={r.status}>{r.status}</StatusPill> },
];

export default function Styleguide() {
  const [dark, setDark] = useState(false);
  const [on, setOn] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set(["1"]));

  return (
    <div data-theme={dark ? "dark" : "light"} className="min-h-screen bg-canvas p-8 text-text">
      <div className="mx-auto max-w-[900px] space-y-10">
        <header className="flex items-center justify-between">
          <h1 className="text-[28px] font-semibold tracking-tight">Warm Operator — Styleguide</h1>
          <Button variant="ghost" onClick={() => setDark((d) => !d)}>
            {dark ? "Light" : "Dark"} mode
          </Button>
        </header>

        <section className="space-y-3">
          <h2 className="font-mono text-[11px] uppercase tracking-wider text-muted">Buttons & pills</h2>
          <div className="flex flex-wrap items-center gap-2">
            <Button>Create draft</Button>
            <Button variant="ghost">Cancel</Button>
            <StatusPill tone="go">Sourced</StatusPill>
            <StatusPill tone="draft">Drafted</StatusPill>
            <StatusPill tone="reply">Replied</StatusPill>
            <Tag>Apollo</Tag>
          </div>
        </section>

        <section className="space-y-3">
          <h2 className="font-mono text-[11px] uppercase tracking-wider text-muted">Inputs</h2>
          <Card className="space-y-3">
            <Input placeholder="Search contacts…" />
            <div className="flex items-center gap-3">
              <Toggle on={on} onToggle={() => setOn((v) => !v)} label="Auto-enrich" />
              <span className="text-[13px]">Auto-enrich</span>
            </div>
          </Card>
        </section>

        <section className="space-y-3">
          <h2 className="font-mono text-[11px] uppercase tracking-wider text-muted">DataTable</h2>
          <Card className="p-0">
            <DataTable
              columns={columns}
              rows={rows}
              getRowId={(r) => r.id}
              selectedIds={selected}
              onToggleRow={(id) =>
                setSelected((s) => {
                  const n = new Set(s);
                  n.has(id) ? n.delete(id) : n.add(id);
                  return n;
                })
              }
              onSort={() => {}}
            />
          </Card>
        </section>

        <section className="space-y-3">
          <h2 className="font-mono text-[11px] uppercase tracking-wider text-muted">Empty state</h2>
          <EmptyState
            title="No contacts in this routine yet"
            description="Run the Fall-26 backend routine and Sourcecado will pull, enrich, and rank candidates."
            action={<Button>Run routine</Button>}
          />
        </section>

        <section className="space-y-3">
          <h2 className="font-mono text-[11px] uppercase tracking-wider text-muted">AppShell (live nav)</h2>
          <div className="overflow-hidden rounded-[12px] border border-border">
            <AppShell
              nav={[{ label: "Research Chat", href: "/chat" }]}
              activeHref="/chat"
              user={{ name: "Fisher X", role: "Sourcing Director" }}
              inspector={<div className="text-[13px] text-muted">Inspector pane</div>}
            >
              <div className="p-6 text-[13px] text-muted">Center content slot</div>
            </AppShell>
          </div>
        </section>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify it builds and renders**

Run:
```bash
npm run build
```
Expected: build succeeds with `/styleguide` in the route list. (Optional manual check: `npm run dev`, open http://localhost:3000/styleguide, toggle dark mode, confirm it matches `/tmp/sourcecado-warm-operator-preview.html`.)

- [ ] **Step 3: Commit**

```bash
git add src/app/styleguide/page.tsx
git commit -m "feat(fd): add /styleguide primitive catalog + visual QA surface"
```

---

### Task 8: Plan amendments — insert FD slice + reframe design gates

Update the task breakdown so the foundation is recorded and downstream slices are reminded to use it.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-15-sourcecado-full-agent-stack-task-breakdown.md`

- [ ] **Step 1: Insert the FD slice after F2**

In the task breakdown, immediately after the F2 slice block (before `## F3 — Model Gateway…`), insert:
```markdown
## FD — Design Foundation (Warm Operator)
Type: AFK · Blocked by: F1 · Done: 2026-06-18

**What to build:** Wire DESIGN.md ("Warm Operator") into the app — Tailwind v4 tokens
+ self-hosted General Sans / Geist Mono, a reusable primitive kit
(`src/components/ui`: AppShell, Button, StatusPill/Tag, DataTable, Input/Toggle, Card,
EmptyState), retrofit of the F1 shell, and a `/styleguide` catalog. All later UI slices
build on these primitives instead of restyling.

**Acceptance criteria:**
- [ ] DESIGN.md tokens are the single source; no raw hex in `src/components` or `src/app/*.tsx`
- [ ] Primitive kit exists with render tests; existing backend suite stays green
- [ ] F1 shell renders on AppShell; UI renamed to Sourcecado; `/styleguide` matches the approved preview

**Tasks:**
- [ ] FD.1 Design tokens + font wiring (~1h) · AFK
- [ ] FD.2 UI test infra + Button/StatusPill (~1.5h) · AFK
- [ ] FD.3 Input/Toggle/Card/EmptyState (~1h) · AFK
- [ ] FD.4 DataTable (~1.5h) · AFK
- [ ] FD.5 AppShell (~1.5h) · AFK
- [ ] FD.6 F1 retrofit + Sourcecado rename (~1h) · AFK
- [ ] FD.7 /styleguide catalog page (~1h) · AFK
```

- [ ] **Step 2: Reframe the four design-review gates**

Reword these four task lines from "design it" to "verify against DESIGN.md + uses the primitives":
- `A6.3 Chat-layout design review pass` → `A6.3 Verify Research Chat matches DESIGN.md + uses src/components/ui primitives (~1h) · HITL`
- `B4.3 Detail-page design review pass` → `B4.3 Verify Contact/Org detail matches DESIGN.md + uses primitives (~1h) · HITL`
- `D1.4 Artifact shape + panel design review` → `D1.4 Verify artifact panel matches DESIGN.md + uses primitives; confirm artifact shape (~1h) · HITL`
- `E2.3 Routine-page design review` → `E2.3 Verify Routine setup page matches DESIGN.md + uses primitives (~1h) · HITL`

- [ ] **Step 3: Add a primitive-reuse reminder to downstream UI tasks**

Append ` (build with src/components/ui primitives)` to these task lines:
- `F4.4 Run inspector view (read-only trace render)`
- `A4.2 Import result UI with per-file status/skip reasons`
- `A6.1 Research Chat UI: message list + input + run trigger`
- `A7.2 Memory management page: list + add + correct`
- `B4.1 Detail page data loaders + layout`
- `B4.2 History/outcomes/artifacts panels`
- `D1.2 Artifact panel UI + draft/revise/save flow`
- `E2.1 Routine setup page (config form)`
- `G2.2 Usage + run-status UI surface`

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-06-15-sourcecado-full-agent-stack-task-breakdown.md
git commit -m "docs(fd): record Design Foundation slice + reframe design-review gates"
```

---

### Task 9: Final verification

Confirm the whole foundation is sound before moving to F3.

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run:
```bash
npm test
```
Expected: all tests pass — pre-existing backend tests plus the 5 new component test files (Button, StatusPill, Input/Toggle, EmptyState, DataTable, AppShell).

- [ ] **Step 2: Build + lint**

Run:
```bash
npm run build && npm run lint
```
Expected: build succeeds; lint reports no errors.

- [ ] **Step 3: Token + naming gates**

Run:
```bash
echo "--- raw hex in components/app tsx (expect none) ---"
grep -rnE "#[0-9a-fA-F]{6}" src/components src/app --include="*.tsx" || echo "CLEAN: no raw hex"
echo "--- SourcyAvo in UI (expect none) ---"
grep -rn "SourcyAvo" src/app src/components || echo "CLEAN: no SourcyAvo in UI"
```
Expected: both `CLEAN`. (The `AvocadoMark` SVG uses `var(--accent)`/`var(--pit)` and `#fff`; `#fff` is 3-digit and won't match the 6-digit pattern — acceptable as the one literal white for the avatar/pit highlight.)

- [ ] **Step 4: Accessibility check — muted-text contrast**

`--muted: #78716C` on `--canvas: #FAF8F3` is ≈ 4.3:1 — passes WCAG AA for large/secondary text (≥18px or ≥14px bold) but is just under the 4.5:1 floor for normal body text. Confirm `text-muted` is used only for metadata/secondary labels (12–13px secondary), never for primary reading copy. If any primary body copy uses `text-muted`, darken the token in `DESIGN.md` + `globals.css` to `#6B6259` (≈ 5.0:1) and rerun the build. Verify by inspecting the `text-muted` usages:
```bash
grep -rn "text-muted" src/components src/app --include="*.tsx"
```
Expected: every hit is a label/metadata/secondary string, not a primary paragraph. Tab through `/styleguide` in a browser and confirm every interactive element shows a visible avocado focus ring.

- [ ] **Step 5: Report status**

Summarize: foundation complete, F1 retrofitted, plan updated, ready for F3. Note the deferred broader "SourcyAvo" rename (docs, CONTEXT.md, `src/extractors/llm.ts`) as a separate decision.

---

## Notes / deferred

- **Broader SourcyAvo rename** (docs, `CONTEXT.md`, `CHANGELOG.md`, `src/extractors/llm.ts:193` prompt string) is intentionally out of scope here — it's content/behavior, not design tone. Flag to the user as a separate cleanup.
- **System dark-mode preference** is not wired (default is `data-theme="light"`; `/styleguide` has a manual toggle). A global theme switcher can be added when a settings surface exists.
- **Nav grows per slice:** add entries to `src/lib/nav.ts` as Contacts/Run Ledger/Drafts/Memory/Routines pages land. `NavItem` carries an optional `icon` — supply per-item icons as those sections gain identity.
- **Mobile nav drawer deferred:** mobile shows a branded top bar only (no nav links). Sourcecado is a desktop-first operator tool; a slide-out drawer is a later enhancement, not part of FD.
- **DataTable row-click semantics (decision for when the inspector lands):** the FD `DataTable` treats a row click as *select* (toggles `selectedIds`). When the Contact/Org inspector arrives (B4 / F4.4), decide the split — e.g. checkbox column = select, row body = open inspector. Resolve then; do not retrofit FD for it.

## GSTACK REVIEW REPORT

Skill: /plan-design-review · Date: 2026-06-18 · Base: main · Plan: this file

| Item | Status |
|------|--------|
| UI scope | Yes — design-foundation (tokens, fonts, primitive kit, F1 retrofit, styleguide) |
| DESIGN.md | Present — plan calibrated against it; high fidelity to the approved Warm Operator preview |
| Mockups | Skipped — generator runtime-blocked (OpenAI org verification) + design already approved via live HTML preview |
| Initial rating | 7.5/10 |
| Final rating | 9/10 |

Findings (6) — fixed inline unless noted:
1. [High a11y] No focus-visible rings on Button/nav → FIXED (Button base + nav links + brand link).
2. [Medium] Nav text-only vs approved preview → FIXED (`NavItem.icon?` + render slot).
3. [Medium] Mobile loses nav + branding → FIXED (mobile top bar with mark; full drawer deferred + documented).
4. [Low] `AvocadoMark` buried in EmptyState but used by AppShell → FIXED (own file `AvocadoMark.tsx`).
5. [Medium a11y] Muted-on-canvas ≈ 4.3:1 < 4.5 → ADDED verification step (Task 9.4) + concrete darker fallback `#6B6259`.
6. [Low] DataTable row-click conflates select/open → DEFERRED with explicit decision note for B4/F4.4.

VERDICT: APPROVED FOR IMPLEMENTATION. No blocking issues; gaps 1–5 closed in-plan, gap 6 is a downstream decision.

NO UNRESOLVED DECISIONS
