import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { getSkills, type SkillInfo } from "../api";

type SkillsState =
  | { status: "loading" }
  | { status: "loaded"; skills: SkillInfo[] }
  | { status: "failed" };

function humanizeSkillName(name: string): string {
  const words = name.trim().replace(/[-_]+/g, " ").replace(/\s+/g, " ");
  return words ? `${words[0].toUpperCase()}${words.slice(1)}` : "Untitled skill";
}

function sourceLabel(source: SkillInfo["source"]): string {
  return source === "builtin" ? "Built into Sourcecado" : "Workspace";
}

function rowSourceLabel(source: SkillInfo["source"]): string {
  return source === "builtin" ? "Built in" : "Workspace";
}

function SkillsLoading() {
  return (
    <section className="skills-layout skills-loading" role="status" aria-label="Loading skills">
      <span className="sr-only">Loading skills…</span>
      <div className="skill-catalog-skeleton" aria-hidden="true">
        <div className="skill-search-skeleton skill-skeleton" />
        {[0, 1, 2].map((item) => (
          <div className="skill-row-skeleton skill-skeleton" key={item} />
        ))}
      </div>
      <div className="skill-detail-skeleton skill-skeleton" aria-hidden="true" />
    </section>
  );
}

function SkillDetail({ skill }: { skill: SkillInfo }) {
  const title = humanizeSkillName(skill.name);
  const headingId = `skill-detail-${skill.name.replace(/[^a-z0-9_-]/gi, "-")}`;
  return (
    <article className="skill-detail" role="region" aria-labelledby={headingId}>
      <header className="skill-detail-header">
        <div>
          <h2 id={headingId}>{title}</h2>
          <div className="skill-meta" aria-label="Skill status and source">
            <span className="skill-ready">Ready</span>
            <span>{sourceLabel(skill.source)}</span>
          </div>
        </div>
        <div className="skill-management" aria-label="Skill management">
          <button type="button" disabled aria-label={`Edit ${title}`}>Edit</button>
          <button
            className="skill-enabled-switch"
            type="button"
            role="switch"
            aria-checked="true"
            aria-label={`Disable ${title}`}
            disabled
          >
            <span aria-hidden="true" />
          </button>
        </div>
      </header>

      <section className="skill-detail-section">
        <h3>Use when</h3>
        <p>{skill.useWhen || "No activation guidance provided."}</p>
      </section>
      <section className="skill-detail-section">
        <h3>What it does</h3>
        <p>{skill.purpose || "No purpose provided."}</p>
      </section>
      <section className="skill-detail-section">
        <h3>Instructions</h3>
        <div className="skill-instructions">
          {skill.instructions ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
              {skill.instructions}
            </ReactMarkdown>
          ) : (
            <p>No instructions provided.</p>
          )}
        </div>
      </section>
    </article>
  );
}

function SkillsCatalog({ skills }: { skills: SkillInfo[] }) {
  const [query, setQuery] = useState("");
  const [selectedName, setSelectedName] = useState(skills[0]?.name ?? "");
  const visibleSkills = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return skills;
    return skills.filter((skill) => (
      `${skill.name} ${humanizeSkillName(skill.name)} ${skill.purpose} ${skill.useWhen} ${sourceLabel(skill.source)}`
        .toLocaleLowerCase()
        .includes(normalized)
    ));
  }, [query, skills]);
  const selected = visibleSkills.find((skill) => skill.name === selectedName) ?? visibleSkills[0];

  return (
    <section className="skills-layout">
      <div className="skill-catalog">
        <div className="skill-search">
          <span aria-hidden="true">⌕</span>
          <input
            type="search"
            aria-label="Search skills"
            placeholder="Search skills"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <p className="skill-section-label">Available · {visibleSkills.length}</p>
        {visibleSkills.length === 0 ? (
          <div className="skill-no-results" role="status">
            <strong>No skills match “{query.trim()}”</strong>
            <button type="button" onClick={() => setQuery("")}>Clear skill search</button>
          </div>
        ) : (
          <ul className="skill-list" aria-label="Available skills">
            {visibleSkills.map((skill) => {
              const title = humanizeSkillName(skill.name);
              const isSelected = selected?.name === skill.name;
              return (
                <li key={skill.name}>
                  <button
                    className="skill-row"
                    type="button"
                    aria-pressed={isSelected}
                    onClick={() => setSelectedName(skill.name)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") setSelectedName(skill.name);
                    }}
                  >
                    <span className="skill-icon" aria-hidden="true">⚡</span>
                    <span className="skill-row-copy">
                      <strong>{title}</strong>
                      <span className="skill-row-subline">
                        <span className="skill-row-purpose">{skill.purpose}</span>
                        <span className="skill-row-context">
                          <span className="skill-row-ready">Ready</span>
                          <span className="skill-row-separator" aria-hidden="true">·</span>
                          <span>{rowSourceLabel(skill.source)}</span>
                        </span>
                      </span>
                    </span>
                    <span className="skill-chevron" aria-hidden="true">›</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      {selected ? <SkillDetail skill={selected} /> : <div className="skill-detail-placeholder" aria-hidden="true" />}
    </section>
  );
}

export function SkillsPage() {
  const [state, setState] = useState<SkillsState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setState({ status: "loading" });
    getSkills().then((body) => {
      if (active) setState({ status: "loaded", skills: body.skills });
    }).catch(() => {
      if (active) setState({ status: "failed" });
    });
    return () => {
      active = false;
    };
  }, [attempt]);

  return (
    <main className="route-page skills-page">
      <header className="skills-page-header">
        <h1>Skills</h1>
        <p>Instructions that extend how Sourcecado handles sourcing work.</p>
      </header>
      {state.status === "loading" ? (
        <SkillsLoading />
      ) : state.status === "failed" ? (
        <section className="route-error" role="alert">
          <h2>The skills catalog couldn’t be loaded</h2>
          <p>Check that Sourcecado is available, then try again.</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            Retry loading skills
          </button>
        </section>
      ) : state.skills.length === 0 ? (
        <section className="route-empty" role="status">
          <h2>No skills available</h2>
          <p>Installed skills will appear here when they’re ready to use.</p>
        </section>
      ) : (
        <SkillsCatalog skills={state.skills} />
      )}
    </main>
  );
}
