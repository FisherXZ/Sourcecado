"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Button, Card, DataTable, EmptyState, Input, StatusPill, type Column } from "@/components/ui";

interface SourceItem {
  sourceId: string;
  title: string | null;
  sourceType: string;
  updatedAt: string;
  archived: boolean;
}

type Tab = "sources" | "note";

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function MemoryClient() {
  const [tab, setTab] = useState<Tab>("sources");

  return (
    <div className="px-6 py-6">
      <header className="mb-5">
        <h1 className="text-xl font-semibold tracking-tight text-text">Memory</h1>
        <p className="mt-1 text-[13px] text-muted">
          Sources the agent can cite, and notes you add by hand. Archiving a wrong source retires it
          from answers without deleting it.
        </p>
      </header>

      <div className="mb-4 flex gap-1">
        <TabButton active={tab === "sources"} onClick={() => setTab("sources")}>
          Sources
        </TabButton>
        <TabButton active={tab === "note"} onClick={() => setTab("note")}>
          Add note
        </TabButton>
      </div>

      {tab === "sources" ? <SourcesTab /> : <AddNoteTab />}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`rounded-[6px] px-3 py-1.5 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-accent-tint ${
        active ? "bg-accent-tint text-accent-deep" : "text-muted hover:text-text"
      }`}
    >
      {children}
    </button>
  );
}

function SourcesTab() {
  const [sources, setSources] = useState<SourceItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/memory/sources");
      const data = (await res.json()) as { sources?: SourceItem[]; error?: string };
      if (data.error) setError(data.error);
      else setSources(data.sources ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load sources");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function toggleArchive(row: SourceItem) {
    setBusyId(row.sourceId);
    try {
      await fetch(`/api/memory/sources/${row.sourceId}/archive`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ archived: !row.archived }),
      });
      await load();
    } finally {
      setBusyId(null);
    }
  }

  if (error) {
    return (
      <Card>
        <div role="alert" className="text-[13px] text-text">
          Error: {error}
        </div>
      </Card>
    );
  }
  if (!sources) {
    return <p className="text-[13px] text-muted">Loading…</p>;
  }
  if (sources.length === 0) {
    return (
      <EmptyState
        title="No sources yet"
        description="Import files or add a note to start building sourcing memory."
      />
    );
  }

  const columns: Column<SourceItem>[] = [
    {
      key: "title",
      header: "Source",
      render: (r) => (
        <div className="flex flex-col py-1.5">
          <span className="font-medium text-text">{r.title ?? r.sourceId}</span>
          <span className="font-mono text-[11px] text-muted">{r.sourceId}</span>
        </div>
      ),
    },
    { key: "sourceType", header: "Type", render: (r) => <span className="text-muted">{r.sourceType}</span> },
    {
      key: "updatedAt",
      header: "Updated",
      render: (r) => (
        <span className="font-mono tabular-nums text-[12px] text-muted">{formatDate(r.updatedAt)}</span>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (r) => (
        <StatusPill tone={r.archived ? "warn" : "go"}>{r.archived ? "Archived" : "Active"}</StatusPill>
      ),
    },
    {
      key: "action",
      header: "",
      render: (r) => (
        <Button variant="ghost" disabled={busyId === r.sourceId} onClick={() => toggleArchive(r)}>
          {r.archived ? "Restore" : "Archive"}
        </Button>
      ),
    },
  ];

  return (
    <div className="overflow-hidden rounded-[8px] border border-border bg-surface">
      <DataTable columns={columns} rows={sources} getRowId={(r) => r.sourceId} />
    </div>
  );
}

function AddNoteTab() {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim() || !text.trim() || saving) return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      const res = await fetch("/api/memory/note", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title, text }),
      });
      const data = (await res.json()) as { sourceId?: string; error?: string };
      if (data.error) setError(data.error);
      else {
        setSaved(data.sourceId ?? null);
        setTitle("");
        setText("");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save note");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="max-w-2xl">
      <form onSubmit={onSubmit} className="flex flex-col gap-3">
        <div>
          <label htmlFor="note-title" className="mb-1 block text-[12px] font-medium text-muted">
            Note title
          </label>
          <Input
            id="note-title"
            placeholder="e.g. Jordan Lee — went cold"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={saving}
          />
        </div>
        <div>
          <label htmlFor="note-text" className="mb-1 block text-[12px] font-medium text-muted">
            Note text
          </label>
          <textarea
            id="note-text"
            rows={5}
            placeholder="What did you learn? A correction supersedes prior info on the same subject."
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={saving}
            className="w-full rounded-[6px] border border-border bg-canvas px-3 py-2 text-[13px] text-text outline-none placeholder:text-muted focus:border-accent focus:ring-[3px] focus:ring-accent-tint"
          />
        </div>
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={saving || !title.trim() || !text.trim()}>
            {saving ? "Saving…" : "Save note"}
          </Button>
          {saved && (
            <span className="text-[13px] text-accent-deep">
              Saved as <span className="font-mono">{saved}</span>
            </span>
          )}
          {error && (
            <span role="alert" className="text-[13px] text-neg-tx">
              {error}
            </span>
          )}
        </div>
      </form>
    </Card>
  );
}
