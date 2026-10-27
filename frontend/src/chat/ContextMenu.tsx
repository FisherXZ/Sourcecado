import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { getBoard, type ActivePerson, type BoardPerson } from "../api";
import { withoutApolloNameMasks } from "../personName";
import { MenuRow } from "./MenuRow";
import type { ContextChip } from "./messageContext";
import {
  FileIcon,
  PersonIcon,
  PlusIcon,
  RetryIcon,
  SearchIcon,
} from "./icons";

type PickerPerson = {
  readonly id: string;
  readonly name: string;
  readonly detail: string;
};

type People =
  | { readonly status: "loading" }
  | { readonly status: "loaded"; readonly people: readonly PickerPerson[] }
  | { readonly status: "failed" };

function toPickerPerson(person: BoardPerson): PickerPerson {
  const name = withoutApolloNameMasks(
    [person.first_name, person.last_name].filter(Boolean).join(" "),
  );
  return {
    id: person.person_id,
    name: name || "Unknown person",
    detail: [person.title, person.company].filter(Boolean).join(" · "),
  };
}

async function loadPeople(): Promise<readonly PickerPerson[]> {
  const board = await getBoard();
  return [...board.open, ...board.in_conversation, ...board.done, ...board.backlog]
    .map(toPickerPerson)
    .sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * The "+" button and its menu: attach a person as a chip above the box.
 *
 * Attaching a file belongs to the attachments work (#253). It shows here as
 * unavailable, so the menu does not offer a control that does nothing.
 */
export function ContextMenu({
  attached,
  boundPerson,
  onAttach,
  onPicked,
}: {
  readonly attached: readonly ContextChip[];
  /** A conversation about one person cannot read any other person file. */
  readonly boundPerson: ActivePerson | null;
  readonly onAttach: (chip: ContextChip) => void;
  /** The menu closed after a pick; the composer takes focus back. */
  readonly onPicked: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"menu" | "people">("menu");
  const [people, setPeople] = useState<People>({ status: "loading" });
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const menuId = useId();
  const listId = useId();

  const choices = useMemo(() => {
    if (people.status !== "loaded") return [];
    const taken = new Set(attached.map((chip) => chip.id));
    const needle = query.trim().toLowerCase();
    return people.people.filter(
      (person) =>
        !taken.has(person.id) &&
        `${person.name} ${person.detail}`.toLowerCase().includes(needle),
    );
  }, [people, attached, query]);

  function close(returnFocus: boolean) {
    setOpen(false);
    setView("menu");
    setQuery("");
    setActive(0);
    if (returnFocus) trigger.current?.focus();
  }

  function fetchPeople() {
    setPeople({ status: "loading" });
    loadPeople().then(
      (loaded) => setPeople({ status: "loaded", people: loaded }),
      () => setPeople({ status: "failed" }),
    );
  }

  function showPeople() {
    setView("people");
    setActive(0);
    fetchPeople();
  }

  function pick(person: PickerPerson) {
    onAttach({ kind: "person", id: person.id, name: person.name });
    close(false);
    onPicked();
  }

  // Focus moves into the menu when it opens, and into the search box when the
  // list of people opens.
  useEffect(() => {
    if (!open) return;
    if (view === "people") search.current?.focus();
    else root.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [open, view]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) close(false);
    };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open]);

  function onMenuKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
      return;
    }
    if (view !== "menu") return;
    const items = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]'),
    );
    const at = items.indexOf(document.activeElement as HTMLElement);
    const move = (to: number) => {
      event.preventDefault();
      items[(to + items.length) % items.length]?.focus();
    };
    if (event.key === "ArrowDown") move(at + 1);
    else if (event.key === "ArrowUp") move(at - 1);
    else if (event.key === "Home") move(0);
    else if (event.key === "End") move(items.length - 1);
    else if (event.key === "Tab") close(false);
  }

  function onSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" && choices.length > 0) {
      event.preventDefault();
      setActive((active + 1) % choices.length);
    } else if (event.key === "ArrowUp" && choices.length > 0) {
      event.preventDefault();
      setActive((active - 1 + choices.length) % choices.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const chosen = choices[active];
      if (chosen) pick(chosen);
    }
  }

  const activeId = choices[active] ? `${listId}-${active}` : undefined;

  return (
    <div className="sourcecado-context-menu-root" ref={root} onKeyDown={onMenuKeyDown}>
      <button
        ref={trigger}
        type="button"
        className="sourcecado-composer-tool"
        aria-label="Add context"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => (open ? close(true) : setOpen(true))}
      >
        <PlusIcon />
      </button>
      {open && view === "menu" ? (
        <div className="sourcecado-menu sourcecado-context-menu" id={menuId} role="menu" aria-label="Add context">
          <button
            type="button"
            role="menuitem"
            className="sourcecado-menu-option"
            aria-disabled={boundPerson ? "true" : undefined}
            onClick={boundPerson ? undefined : showPeople}
          >
            <MenuRow
              icon={<PersonIcon />}
              title="Attach a person"
              detail={
                boundPerson
                  ? `This conversation is about ${boundPerson.label}`
                  : "From your Board"
              }
            />
          </button>
          <button
            type="button"
            role="menuitem"
            className="sourcecado-menu-option"
            aria-disabled="true"
          >
            <MenuRow icon={<FileIcon />} title="Attach a file" detail="Not available yet" />
          </button>
        </div>
      ) : null}
      {open && view === "people" ? (
        <div className="sourcecado-menu sourcecado-people-menu">
          <div className="sourcecado-menu-search">
            <SearchIcon />
            <input
              ref={search}
              type="text"
              role="combobox"
              aria-label="Search people"
              aria-expanded="true"
              aria-controls={listId}
              aria-activedescendant={activeId}
              placeholder="Search people"
              value={query}
              onChange={(event) => {
                setQuery(event.currentTarget.value);
                setActive(0);
              }}
              onKeyDown={onSearchKeyDown}
            />
          </div>
          {people.status === "loading" ? (
            <p className="sourcecado-menu-status" role="status">Loading people…</p>
          ) : people.status === "failed" ? (
            <>
              <p className="sourcecado-menu-status" role="alert">People did not load.</p>
              <button type="button" className="sourcecado-menu-retry" onClick={fetchPeople}>
                <RetryIcon />
                Try again
              </button>
            </>
          ) : choices.length === 0 ? (
            <p className="sourcecado-menu-status" role="status">
              {people.people.length === 0
                ? "No people are on your Board yet."
                : "No person matches."}
            </p>
          ) : (
            <ul id={listId} role="listbox" aria-label="People">
              {choices.map((person, index) => (
                <li
                  key={person.id}
                  id={`${listId}-${index}`}
                  role="option"
                  aria-selected={index === active}
                  className="sourcecado-menu-option"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => pick(person)}
                >
                  <MenuRow icon={<PersonIcon />} title={person.name} detail={person.detail} />
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
