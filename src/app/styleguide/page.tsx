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
          <h2 className="font-mono text-[11px] uppercase tracking-wider text-muted">Buttons &amp; pills</h2>
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
                  if (n.has(id)) n.delete(id);
                  else n.add(id);
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
