import { useCallback, useEffect, useState } from "react";

import { getSkills, type SkillInfo } from "../api";
import { RetryIcon, SkillIcon } from "./icons";
import { MenuRow } from "./MenuRow";

type SkillCatalog =
  | { readonly status: "idle" }
  | { readonly status: "loading" }
  | { readonly status: "loaded"; readonly skills: readonly SkillInfo[] }
  | { readonly status: "failed" };

/** The text before the first space, when the box starts with "/". Else null. */
export function slashQuery(text: string): string | null {
  const match = /^\/(\S*)$/.exec(text);
  return match ? match[1] : null;
}

/**
 * The installed skills, read from the same endpoint as the Skills page.
 * They load the first time the menu is wanted and stay for this composer.
 */
export function useSkillCatalog(wanted: boolean) {
  const [catalog, setCatalog] = useState<SkillCatalog>({ status: "idle" });
  const load = useCallback(() => {
    setCatalog({ status: "loading" });
    getSkills().then(
      (result) => setCatalog({ status: "loaded", skills: result.skills }),
      () => setCatalog({ status: "failed" }),
    );
  }, []);
  useEffect(() => {
    if (wanted && catalog.status === "idle") load();
  }, [wanted, catalog.status, load]);
  return { catalog, retry: load };
}

/** Skills whose name or purpose contains the query, name matches first. */
export function matchSkills(
  skills: readonly SkillInfo[],
  query: string,
): SkillInfo[] {
  const needle = query.toLowerCase();
  const rank = (skill: SkillInfo) => {
    const name = skill.name.toLowerCase();
    if (name.startsWith(needle)) return 0;
    if (name.includes(needle)) return 1;
    return skill.purpose.toLowerCase().includes(needle) ? 2 : 3;
  };
  return skills.filter((skill) => rank(skill) < 3).sort((a, b) => rank(a) - rank(b));
}

export function skillOptionId(menuId: string, index: number): string {
  return `${menuId}-option-${index}`;
}

export function SlashMenu({
  id,
  catalog,
  query,
  items,
  activeIndex,
  onPick,
  onRetry,
}: {
  readonly id: string;
  readonly catalog: SkillCatalog;
  readonly query: string;
  readonly items: readonly SkillInfo[];
  readonly activeIndex: number;
  readonly onPick: (skill: SkillInfo) => void;
  readonly onRetry: () => void;
}) {
  if (catalog.status === "idle" || catalog.status === "loading") {
    return (
      <div className="sourcecado-menu sourcecado-slash-menu">
        <p className="sourcecado-menu-status" role="status">
          Loading skills…
        </p>
      </div>
    );
  }
  if (catalog.status === "failed") {
    return (
      <div className="sourcecado-menu sourcecado-slash-menu">
        <p className="sourcecado-menu-status" role="alert">
          Skills did not load.
        </p>
        <button
          type="button"
          className="sourcecado-menu-retry"
          onMouseDown={(event) => event.preventDefault()}
          onClick={onRetry}
        >
          <RetryIcon />
          Try again
        </button>
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div className="sourcecado-menu sourcecado-slash-menu">
        <p className="sourcecado-menu-status" role="status">
          {query ? `No skill matches /${query}` : "No skills are installed."}
        </p>
      </div>
    );
  }
  return (
    <div className="sourcecado-menu sourcecado-slash-menu">
      <ul id={id} role="listbox" aria-label="Skills">
        {items.map((skill, index) => (
          <li
            key={skill.name}
            id={skillOptionId(id, index)}
            role="option"
            aria-selected={index === activeIndex}
            className="sourcecado-menu-option"
            // Keep focus in the text box: the click must not blur it.
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => onPick(skill)}
          >
            <MenuRow
              icon={<SkillIcon />}
              title={`/${skill.name}`}
              detail={skill.purpose || skill.useWhen || "No description."}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}
