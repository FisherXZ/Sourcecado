import { useEffect, useState } from "react";

import { getSkills, type SkillInfo } from "../api";

type SkillsState =
  | { status: "loading" }
  | { status: "loaded"; skills: SkillInfo[] }
  | { status: "failed" };

function humanizeSkillName(name: string): string {
  const words = name.trim().replace(/[-_]+/g, " ").replace(/\s+/g, " ");
  return words ? `${words[0].toUpperCase()}${words.slice(1)}` : "Untitled skill";
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
      <h1>Skills</h1>
      {state.status === "loading" ? (
        <p role="status">Loading skills…</p>
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
        <ul className="skill-list" aria-label="Available skills">
          {state.skills.map((skill, index) => (
            <li key={`${skill.name}-${index}`}>
              <article className="skill-card">
                <h2>{humanizeSkillName(skill.name)}</h2>
                <p>{skill.description}</p>
              </article>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
