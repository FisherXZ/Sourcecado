import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  createSkill,
  deleteSkill,
  getSkills,
  updateSkill,
  type SkillDraft,
  type SkillInfo,
} from "../api";
import { Icon } from "../icons";

type SkillsState =
  | { status: "loading" }
  | { status: "loaded"; skills: SkillInfo[] }
  | { status: "failed" };

type Editing = { draft: SkillDraft; isNew: boolean } | null;

const EMPTY_DRAFT: SkillDraft = { name: "", purpose: "", useWhen: "", instructions: "" };

export function humanizeSkillName(name: string): string {
  const words = name.trim().replace(/[-_]+/g, " ").replace(/\s+/g, " ");
  return words ? `${words[0].toUpperCase()}${words.slice(1)}` : "Untitled skill";
}

/** Typed names become the slash command: lowercase words joined by hyphens. */
function slugify(value: string): string {
  return value.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "").slice(0, 48);
}

function draftOf(skill: SkillInfo): SkillDraft {
  return {
    name: skill.name,
    purpose: skill.purpose,
    useWhen: skill.useWhen,
    instructions: skill.instructions,
  };
}

function SourcePill({ skill }: { skill: SkillInfo }) {
  if (skill.source === "builtin") return <span className="sm-pill">Built in</span>;
  if (skill.builtinDefault) return <span className="sm-pill">Edited</span>;
  return null;
}

function SkillView({
  skill,
  onEdit,
  onDelete,
}: {
  skill: SkillInfo;
  onEdit: () => void;
  onDelete: () => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const resets = skill.builtinDefault;
  const removable = skill.source === "workspace";
  const verb = resets ? "Reset" : "Delete";

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await onDelete();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Couldn’t ${verb.toLowerCase()}.`);
      setBusy(false);
    }
  }

  return (
    <article className="sk-detail" aria-labelledby="sk-detail-title">
      <header className="sk-detail-header">
        <div className="sk-detail-heading">
          <h2 id="sk-detail-title">{humanizeSkillName(skill.name)}</h2>
          <code className="sk-command">/{skill.name}</code>
          <SourcePill skill={skill} />
        </div>
        <div className="sk-detail-actions">
          {confirming ? (
            <>
              <span className="sk-confirm-text">{verb} /{skill.name}?</span>
              <button
                type="button"
                className="sm-btn sm-btn-danger"
                disabled={busy}
                onClick={() => void remove()}
              >
                <Icon name={resets ? "refresh" : "trash"} />
                {verb}
              </button>
              <button
                type="button"
                className="sm-btn"
                disabled={busy}
                onClick={() => setConfirming(false)}
              >
                <Icon name="close" />
                Cancel
              </button>
            </>
          ) : (
            <>
              <button type="button" className="sm-btn" onClick={onEdit}>
                <Icon name="edit" />
                Edit
              </button>
              {removable ? (
                <button
                  type="button"
                  className="sm-btn sm-btn-quiet"
                  onClick={() => setConfirming(true)}
                  title={resets ? "Go back to the built-in version" : undefined}
                >
                  <Icon name={resets ? "refresh" : "trash"} />
                  {verb}
                </button>
              ) : null}
            </>
          )}
        </div>
      </header>
      {error ? <p className="sm-error" role="alert">{error}</p> : null}
      {skill.purpose ? <p className="sk-purpose">{skill.purpose}</p> : null}
      {skill.useWhen ? (
        <p className="sk-use-when" title="Use when">
          <Icon name="chat" />
          <span>{skill.useWhen}</span>
        </p>
      ) : null}
      <div className="sk-instructions">
        <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
          {skill.instructions}
        </ReactMarkdown>
      </div>
    </article>
  );
}

function SkillEditor({
  editing,
  onChange,
  onSave,
  onCancel,
}: {
  editing: NonNullable<Editing>;
  onChange: (draft: SkillDraft) => void;
  onSave: () => Promise<void>;
  onCancel: () => void;
}) {
  const { draft, isNew } = editing;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const first = useRef<HTMLInputElement>(null);
  const ready = Boolean(draft.name && draft.purpose.trim() && draft.instructions.trim());

  useEffect(() => {
    first.current?.focus();
  }, [isNew]);

  function set(field: keyof SkillDraft, value: string) {
    setError(null);
    onChange({ ...draft, [field]: value });
  }

  async function save() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onSave();
    } catch (cause) {
      // A refused save keeps every field as typed.
      setError(cause instanceof Error ? cause.message : "Couldn’t save. Try again.");
      setBusy(false);
    }
  }

  return (
    <form
      className="sk-detail sk-editor"
      aria-label={isNew ? "New skill" : `Edit /${draft.name}`}
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <div className="sk-editor-name">
        <span aria-hidden="true">/</span>
        {isNew ? (
          <input
            ref={first}
            aria-label="Skill name"
            placeholder="skill-name"
            value={draft.name}
            maxLength={48}
            onChange={(event) => set("name", slugify(event.target.value))}
          />
        ) : (
          <code>{draft.name}</code>
        )}
      </div>
      <input
        ref={isNew ? undefined : first}
        className="sk-editor-purpose"
        aria-label="Description"
        placeholder="What it does"
        value={draft.purpose}
        onChange={(event) => set("purpose", event.target.value)}
      />
      <label className="sk-editor-use-when" title="Use when">
        <Icon name="chat" />
        <input
          aria-label="Use when"
          placeholder="When to use it"
          value={draft.useWhen}
          onChange={(event) => set("useWhen", event.target.value)}
        />
      </label>
      <textarea
        className="sk-editor-instructions"
        aria-label="Instructions"
        placeholder="Steps the assistant follows. Markdown works."
        rows={14}
        value={draft.instructions}
        onChange={(event) => set("instructions", event.target.value)}
      />
      {error ? <p className="sm-error" role="alert">{error}</p> : null}
      <div className="sk-editor-actions">
        <button type="submit" className="sm-btn sm-btn-primary" disabled={!ready || busy}>
          <Icon name="save" />
          {busy ? "Saving…" : "Save"}
        </button>
        <button type="button" className="sm-btn" disabled={busy} onClick={onCancel}>
          <Icon name="close" />
          Cancel
        </button>
      </div>
    </form>
  );
}

function SkillsLoading() {
  return (
    <div className="sk-layout" role="status" aria-label="Loading skills">
      <span className="sm-visually-hidden">Loading skills…</span>
      <div className="sk-list-pane" aria-hidden="true">
        {[0, 1, 2].map((item) => (
          <div className="sm-skeleton sk-row-skeleton" key={item} />
        ))}
      </div>
      <div className="sk-detail" aria-hidden="true">
        <div className="sm-skeleton sk-detail-skeleton" />
      </div>
    </div>
  );
}

export function SkillsPage() {
  const [state, setState] = useState<SkillsState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  const [selectedName, setSelectedName] = useState("");
  const [editing, setEditing] = useState<Editing>(null);

  useEffect(() => {
    let active = true;
    setState({ status: "loading" });
    getSkills().then(
      (body) => {
        if (active) setState({ status: "loaded", skills: body.skills });
      },
      () => {
        if (active) setState({ status: "failed" });
      },
    );
    return () => {
      active = false;
    };
  }, [attempt]);

  const skills = state.status === "loaded" ? state.skills : [];
  const visible = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return skills;
    return skills.filter((skill) =>
      `${skill.name} ${skill.purpose} ${skill.useWhen}`.toLocaleLowerCase().includes(needle),
    );
  }, [query, skills]);
  const selected = visible.find((skill) => skill.name === selectedName) ?? visible[0];

  async function reloadAndSelect(name: string) {
    const body = await getSkills();
    setState({ status: "loaded", skills: body.skills });
    setSelectedName(name);
  }

  async function save() {
    if (!editing) return;
    const saved = editing.isNew
      ? await createSkill(editing.draft)
      : await updateSkill(editing.draft);
    const known = skills.some((row) => row.name === saved.name);
    setState({
      status: "loaded",
      skills: known
        ? skills.map((row) => (row.name === saved.name ? saved : row))
        : [...skills, saved],
    });
    setSelectedName(saved.name);
    setEditing(null);
    setQuery("");
  }

  async function remove(skill: SkillInfo) {
    await deleteSkill(skill.name);
    await reloadAndSelect(skill.builtinDefault ? skill.name : "");
  }

  let body;
  if (state.status === "loading") {
    body = <SkillsLoading />;
  } else if (state.status === "failed") {
    body = (
      <div className="sm-empty" role="alert">
        <p>Skills didn’t load.</p>
        <button type="button" className="sm-btn" onClick={() => setAttempt((value) => value + 1)}>
          <Icon name="refresh" />
          Retry
        </button>
      </div>
    );
  } else if (skills.length === 0 && !editing) {
    body = (
      <div className="sm-empty" role="status">
        <Icon name="skill" />
        <p>No skills yet</p>
        <button
          type="button"
          className="sm-btn sm-btn-primary"
          onClick={() => setEditing({ draft: EMPTY_DRAFT, isNew: true })}
        >
          <Icon name="add" />
          New skill
        </button>
      </div>
    );
  } else {
    const listPane = skills.length === 0 ? null : (
      <div className="sk-list-pane">
        <label className="sm-search">
          <Icon name="search" />
          <input
            type="search"
            aria-label="Search skills"
            placeholder="Search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        {visible.length === 0 ? (
          <p className="sk-no-match" role="status">
            No match
            <button type="button" className="sm-btn sm-btn-quiet" onClick={() => setQuery("")}>
              <Icon name="close" />
              Clear
            </button>
          </p>
        ) : (
          <ul className="sk-list" aria-label="Skills">
            {visible.map((skill) => (
              <li key={skill.name}>
                <button
                  type="button"
                  className="sk-row"
                  aria-current={!editing?.isNew && selected?.name === skill.name ? "true" : undefined}
                  onClick={() => {
                    setEditing(null);
                    setSelectedName(skill.name);
                  }}
                >
                  <Icon name="skill" />
                  <span className="sk-row-text">
                    <strong>{humanizeSkillName(skill.name)}</strong>
                    <span>{skill.purpose}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
    body = (
      <div className={`sk-layout${skills.length === 0 ? " is-single" : ""}`}>
        {listPane}
        {editing ? (
          <SkillEditor
            key={editing.isNew ? "new" : editing.draft.name}
            editing={editing}
            onChange={(draft) => setEditing({ ...editing, draft })}
            onSave={save}
            onCancel={() => setEditing(null)}
          />
        ) : selected ? (
          <SkillView
            key={`${selected.name}:${selected.source}`}
            skill={selected}
            onEdit={() => setEditing({ draft: draftOf(selected), isNew: false })}
            onDelete={() => remove(selected)}
          />
        ) : (
          <div className="sk-detail" aria-hidden="true" />
        )}
      </div>
    );
  }

  return (
    <main className="route-page sm-page">
      <header className="sm-titlebar">
        <div className="sm-heading">
          <h1>Skills</h1>
          {state.status === "loaded" ? <span className="sm-count">{skills.length}</span> : null}
        </div>
        {skills.length > 0 ? (
          <button
            type="button"
            className="sm-btn sm-btn-primary"
            onClick={() => setEditing({ draft: EMPTY_DRAFT, isNew: true })}
          >
            <Icon name="add" />
            New skill
          </button>
        ) : null}
      </header>
      {body}
    </main>
  );
}
