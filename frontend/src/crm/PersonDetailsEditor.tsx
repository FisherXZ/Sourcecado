import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { Icon, type IconName } from "../icons";
import { Glyph, type GlyphName } from "../generative/ResultCard";
import {
  getPerson,
  newOperationId,
  PersonCorrectionError,
  reviewPersonCorrection,
  savePersonCorrection,
  undoPersonCorrection,
  type CrmChange,
  type PersonCorrectionInput,
  type PersonCorrectionReview,
  type PersonDetailField,
  type PersonFile,
} from "../api";

const FIELDS: Array<{
  field: PersonDetailField;
  label: string;
  max: number;
  type?: string;
}> = [
  { field: "first_name", label: "First name", max: 100 },
  { field: "last_name", label: "Last name", max: 150 },
  { field: "title", label: "Role", max: 200 },
  { field: "company", label: "Company", max: 200 },
  { field: "email", label: "Email", max: 254, type: "email" },
  { field: "phone", label: "Phone", max: 80, type: "tel" },
  { field: "linkedin_url", label: "LinkedIn URL", max: 500, type: "url" },
];
const meta = (field: PersonDetailField) =>
  FIELDS.find((item) => item.field === field)!;

/** Role and phone save on Enter. Name, company, email and LinkedIn decide who
 *  this person is, so a change to them always opens the correction review. */
const REVIEWED = new Set<PersonDetailField>([
  "first_name",
  "last_name",
  "company",
  "email",
  "linkedin_url",
]);

/** One field edited in place, or every field ("Edit details"). */
type Scope = PersonDetailField | "all";
type Draft = {
  version: number;
  values: Record<PersonDetailField, string>;
  changed: PersonDetailField[];
  reason: string;
  scope: Scope;
};
function values(
  person: PersonFile["person"],
): Record<PersonDetailField, string> {
  return Object.fromEntries(
    FIELDS.map(({ field }) => [
      field,
      typeof person[field] === "string" ? person[field] : "",
    ]),
  ) as Record<PersonDetailField, string>;
}
const key = (pid: string) => `sourcecado:person-correction:${pid}`;
export function clearPersonCorrectionDraft(pid: string) {
  try {
    localStorage.removeItem(key(pid));
  } catch {
    /* An unavailable draft store must not block an explicit save. */
  }
}
function read(pid: string): Draft | null {
  try {
    const raw = JSON.parse(localStorage.getItem(key(pid)) ?? "null");
    return raw &&
      typeof raw.version === "number" &&
      raw.values &&
      Array.isArray(raw.changed) &&
      typeof raw.reason === "string"
      ? {
          ...raw,
          scope:
            raw.scope === "all" || FIELDS.some((f) => f.field === raw.scope)
              ? raw.scope
              : "all",
        }
      : null;
  } catch {
    return null;
  }
}

/** Person File attributes, in sidebar order. A value speaks for itself. */
const ATTRIBUTES: Array<{
  field: PersonDetailField;
  noun: string;
  icon: { glyph: GlyphName } | { icon: IconName };
}> = [
  { field: "email", noun: "email", icon: { glyph: "mail" } },
  { field: "phone", noun: "phone", icon: { glyph: "phone" } },
  { field: "linkedin_url", noun: "LinkedIn", icon: { glyph: "link" } },
  { field: "company", noun: "company", icon: { icon: "company" } },
  { field: "title", noun: "role", icon: { icon: "role" } },
];

function Mark({ field }: { field: PersonDetailField }) {
  const icon = ATTRIBUTES.find((item) => item.field === field)?.icon ?? {
    glyph: "user" as const,
  };
  return "glyph" in icon ? <Glyph name={icon.glyph} /> : <Icon name={icon.icon} />;
}

function shownValue(field: PersonDetailField, value: string): string {
  return field === "linkedin_url"
    ? value.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")
    : value;
}

function CopyValue({ value, noun }: { value: string; noun: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(timer);
  }, [copied]);
  const label = copied ? `Copied ${noun}` : `Copy ${noun}`;
  return (
    <button
      type="button"
      className="pf-icon-btn pd-hover-action"
      aria-label={label}
      title={label}
      onClick={() =>
        void navigator.clipboard
          ?.writeText(value)
          .then(() => setCopied(true))
          .catch(() => {
            /* The value stays selectable. */
          })
      }
    >
      <Icon name={copied ? "check" : "copy"} />
    </button>
  );
}

export type PersonDetailsHandle = {
  /** Edit one field in place, or every field. Keeps an open unsaved edit. */
  begin: (field?: PersonDetailField) => void;
};

/** One correction editor serves the compact preview and durable Person File. */
export const PersonDetailsEditor = forwardRef<
  PersonDetailsHandle,
  {
    person: PersonFile["person"];
    onSaved: (person: PersonFile["person"]) => void;
    history?: CrmChange[];
    onBusyChange?: (busy: boolean) => void;
    /** The edit button when it lives outside (the Person File header); focus
     *  returns there after an edit. Without it, the heading has its own. */
    editTrigger?: RefObject<HTMLButtonElement | null>;
  }
>(function PersonDetailsEditor(
  { person, onSaved, history = [], onBusyChange, editTrigger },
  ref,
) {
  const pid = person.person_id;
  const [draft, setDraft] = useState<Draft | null>(() => read(pid));
  const [review, setReview] = useState<PersonCorrectionReview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [undoTarget, setUndoTarget] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [sourceRefs, setSourceRefs] = useState<string[]>([]);
  const [saved, setSaved] = useState<{
    fields: PersonDetailField[];
    changeId: string | null;
    undone?: boolean;
  } | null>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(Boolean(draft));
  const editor = useRef<HTMLElement>(null);
  const operation = useRef<{ fingerprint: string; id: string } | null>(null);
  const focusField = useRef<PersonDetailField | null>(null);
  const returnTo = useRef<Scope>(draft?.scope ?? "all");
  // Set while a change is being proposed, so the blur that follows Enter (or
  // the review taking focus) does not propose it a second time.
  const proposing = useRef(false);
  const controls = person.identity_field_controls as
    | Record<string, { protected?: boolean }>
    | undefined;
  const conflicts = Array.isArray(person.identity_conflicts)
    ? (person.identity_conflicts as Array<{
        conflict_id: string;
        field: PersonDetailField;
        provider_value: string | null;
        saved_value: string | null;
        source_ref_id?: string | null;
      }>)
    : [];
  const dirty = Boolean(draft && (draft.changed.length || draft.reason));
  const protectedFields = Object.values(controls ?? {}).some(
    (c) => c.protected,
  );
  const savedValue = (field: PersonDetailField) =>
    typeof person[field] === "string" ? (person[field] as string).trim() : "";
  function changes(d: Draft) {
    return Object.fromEntries(
      d.changed
        .filter((field) => d.values[field].trim() !== savedValue(field))
        .map((field) => [field, d.values[field].trim() || null]),
    ) as Partial<Record<PersonDetailField, string | null>>;
  }

  useEffect(() => {
    if (draft) {
      try {
        localStorage.setItem(key(pid), JSON.stringify(draft));
      } catch {
        /* Keep the in-memory edit available. */
      }
    }
  }, [draft, pid]);
  useEffect(() => {
    if (draft && !review)
      editor.current
        ?.querySelector<HTMLInputElement>(
          focusField.current
            ? `input[name="${focusField.current}"]`
            : "input[name]",
        )
        ?.focus();
    focusField.current = null;
  }, [draft?.scope, Boolean(draft), Boolean(review)]);
  useEffect(() => {
    if (!review) return;
    const card = editor.current?.querySelector(".pd-review");
    (
      card?.querySelector<HTMLElement>('input[type="checkbox"]') ??
      card?.querySelector<HTMLElement>("input.pd-reason") ??
      card?.querySelector<HTMLElement>(".pd-btn.is-primary")
    )?.focus();
  }, [review]);
  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);
  useEffect(() => {
    if (draft) {
      wasEditing.current = true;
      return;
    }
    if (!wasEditing.current) return;
    wasEditing.current = false;
    // Return focus only when it was lost with the closed editor; never pull
    // it back from wherever the director has moved on to.
    const active = document.activeElement;
    if (!active || active === document.body) focusTrigger(returnTo.current);
  }, [Boolean(draft)]);
  useEffect(() => {
    if (!saved) return;
    const timer = window.setTimeout(() => setSaved(null), 8000);
    return () => window.clearTimeout(timer);
  }, [saved]);
  useImperativeHandle(ref, () => ({ begin: (field) => open(field ?? "all") }));

  function focusTrigger(scope: Scope) {
    const target =
      scope === "all" || !ATTRIBUTES.some((a) => a.field === scope)
        ? (editTrigger?.current ?? editButton.current)
        : editor.current?.querySelector<HTMLElement>(
            `[data-edit-field="${scope}"]`,
          );
    target?.focus();
  }
  function open(requested: Scope) {
    if (busy) return;
    if (draft && (Object.keys(changes(draft)).length || review)) {
      // An unsaved edit stays where it is; bring the director back to it.
      editor.current
        ?.querySelector<HTMLElement>(
          review ? ".pd-review input, .pd-review button" : "input[name]",
        )
        ?.focus();
      return;
    }
    const scope: Scope = ATTRIBUTES.some((a) => a.field === requested)
      ? requested
      : "all";
    focusField.current = requested === "all" ? null : requested;
    returnTo.current = scope;
    proposing.current = false;
    setSaved(null);
    setUndoTarget(null);
    setDraft({
      version: person.version ?? 0,
      values: values(person),
      changed: [],
      reason: "",
      scope,
    });
    setReview(null);
    setError(null);
    setStale(false);
  }
  function cancel() {
    clearPersonCorrectionDraft(pid);
    proposing.current = false;
    setDraft(null);
    setReview(null);
    setConfirmed(false);
    setError(null);
    setStale(false);
    setSourceRefs([]);
    setUndoTarget(null);
    operation.current = null;
  }
  function closeReview() {
    proposing.current = false;
    focusField.current = draft && draft.scope !== "all" ? draft.scope : null;
    setReview(null);
    setConfirmed(false);
    setError(null);
  }
  /** Enter, blur or Save: review identity changes, save the rest. */
  function propose(d: Draft | null = draft) {
    if (!d || busy || review || proposing.current) return;
    const changed = Object.keys(changes(d)) as PersonDetailField[];
    if (!changed.length) {
      if (d.scope !== "all") cancel();
      return;
    }
    proposing.current = true;
    if (changed.some((field) => REVIEWED.has(field))) void prepare(d);
    else void save(d, null);
  }
  async function prepare(d: Draft) {
    setBusy(true);
    setError(null);
    try {
      const result = await reviewPersonCorrection(pid, {
        changes: changes(d),
        expected_version: d.version,
      });
      setReview(result);
      setConfirmed(false);
    } catch (e) {
      proposing.current = false;
      setError(
        e instanceof Error
          ? e.message
          : "Couldn’t review. Your edits are kept.",
      );
      if (e instanceof PersonCorrectionError && e.status === 409)
        setStale(true);
    } finally {
      setBusy(false);
    }
  }
  async function reload() {
    if (!draft || busy) return;
    setBusy(true);
    try {
      const latest = await getPerson(pid);
      setDraft({ ...draft, version: latest.person.version ?? 0 });
      proposing.current = false;
      setReview(null);
      setError(null);
      setStale(false);
      operation.current = null;
    } catch {
      setError("Couldn’t load the latest saved details. Your edits are kept.");
    } finally {
      setBusy(false);
    }
  }
  async function save(
    d: Draft | null = draft,
    r: PersonCorrectionReview | null = review,
  ) {
    if (!d || busy || (r?.duplicates.length && !confirmed)) return;
    const input: Omit<PersonCorrectionInput, "operation_id"> = {
      changes: undoTarget ? {} : changes(d),
      expected_version: d.version,
      reason: d.reason.trim(),
      source_refs: sourceRefs,
      ...(r?.duplicates.length
        ? {
            collision_token: r.collision_token,
            confirmed_duplicate_ids: r.duplicates.map((p) => p.person_id),
          }
        : {}),
    };
    const fingerprint = JSON.stringify({ input, undoTarget });
    if (operation.current?.fingerprint !== fingerprint)
      operation.current = { fingerprint, id: newOperationId() };
    setBusy(true);
    setError(null);
    try {
      const result = undoTarget
        ? await undoPersonCorrection(pid, undoTarget, {
            operation_id: operation.current.id,
            collision_token: input.collision_token,
            confirmed_duplicate_ids: input.confirmed_duplicate_ids,
          })
        : await savePersonCorrection(pid, {
            ...input,
            operation_id: operation.current.id,
          });
      const fields = undoTarget
        ? (r?.changes.map((change) => change.field) ?? [])
        : (Object.keys(input.changes) as PersonDetailField[]);
      clearPersonCorrectionDraft(pid);
      proposing.current = false;
      setDraft(null);
      setReview(null);
      setUndoTarget(null);
      operation.current = null;
      setSourceRefs([]);
      setSaved({
        fields,
        changeId:
          !undoTarget && "change" in result
            ? ((result as { change?: CrmChange }).change?.change_id ?? null)
            : null,
        undone: Boolean(undoTarget),
      });
      onSaved(result.person);
      window.dispatchEvent(new Event("sourcecado:board-changed"));
    } catch (e) {
      proposing.current = false;
      setError(
        e instanceof Error ? e.message : "Couldn’t save. Your edits are kept.",
      );
      if (e instanceof PersonCorrectionError && e.review) {
        // A possible duplicate turns a direct save into a review.
        proposing.current = true;
        setReview(e.review);
        setConfirmed(false);
        operation.current = null;
      } else if (e instanceof PersonCorrectionError && e.status === 409)
        setStale(true);
    } finally {
      setBusy(false);
    }
  }
  async function undo(changeId: string) {
    if (busy) return;
    const fingerprint = `undo:${changeId}`;
    if (operation.current?.fingerprint !== fingerprint)
      operation.current = { fingerprint, id: newOperationId() };
    setBusy(true);
    setError(null);
    try {
      const result = await undoPersonCorrection(pid, changeId, {
        operation_id: operation.current.id,
      });
      operation.current = null;
      setSaved((current) => ({
        fields: current?.changeId === changeId ? current.fields : [],
        changeId: null,
        undone: true,
      }));
      onSaved(result.person);
      window.dispatchEvent(new Event("sourcecado:board-changed"));
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Couldn’t undo this correction.",
      );
      if (e instanceof PersonCorrectionError && e.review) {
        const next = values(person);
        for (const change of e.review.changes)
          next[change.field] = change.after ?? "";
        const only = e.review.changes.length === 1 ? e.review.changes[0].field : null;
        setSaved(null);
        setDraft({
          version: person.version ?? 0,
          values: next,
          changed: e.review.changes.map((change) => change.field),
          reason: "Undo correction",
          scope: only && ATTRIBUTES.some((a) => a.field === only) ? only : "all",
        });
        proposing.current = true;
        setReview(e.review);
        setConfirmed(false);
        setUndoTarget(changeId);
        operation.current = null;
      }
    } finally {
      setBusy(false);
    }
  }
  function reviewConflict(conflict: (typeof conflicts)[number]) {
    const d: Draft = {
      version: person.version ?? 0,
      values: { ...values(person), [conflict.field]: conflict.provider_value ?? "" },
      changed: [conflict.field],
      reason: "Reviewed provider suggestion",
      scope: ATTRIBUTES.some((a) => a.field === conflict.field)
        ? conflict.field
        : "all",
    };
    returnTo.current = d.scope;
    setSaved(null);
    setDraft(d);
    setSourceRefs(conflict.source_ref_id ? [conflict.source_ref_id] : []);
    setError(null);
    proposing.current = true;
    void prepare(d);
  }

  function edit(field: PersonDetailField, value: string) {
    if (!draft) return;
    setDraft({
      ...draft,
      values: { ...draft.values, [field]: value },
      changed: draft.changed.includes(field)
        ? draft.changed
        : [...draft.changed, field],
    });
    setError(null);
    operation.current = null;
  }
  function input(field: PersonDetailField, single: boolean) {
    if (!draft) return null;
    const { label, max, type } = meta(field);
    return (
      <input
        name={field}
        type={type ?? "text"}
        aria-label={label}
        placeholder={label}
        autoComplete="off"
        maxLength={max}
        value={draft.values[field]}
        readOnly={busy || Boolean(review)}
        onChange={(e) => edit(field, e.target.value)}
        onKeyDown={
          single
            ? (e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  propose();
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  e.stopPropagation();
                  cancel();
                }
              }
            : undefined
        }
        onBlur={
          single
            ? (e) => {
                if (
                  e.currentTarget
                    .closest("li")
                    ?.contains(e.relatedTarget as Node | null)
                )
                  return;
                propose();
              }
            : undefined
        }
      />
    );
  }

  const reviewCard: ReactNode =
    review && draft ? (
      <div
        className="pd-review"
        role="group"
        aria-label={undoTarget ? "Review Undo" : "Review person correction"}
        onKeyDown={(e: KeyboardEvent) => {
          if (e.key !== "Escape") return;
          e.preventDefault();
          e.stopPropagation();
          if (undoTarget) cancel();
          else closeReview();
        }}
      >
        <ul className="pd-review-changes" aria-label="Person detail changes">
          {review.changes.map((change) => (
            <li key={change.field}>
              <Mark field={change.field} />
              <span className="pf-visually-hidden">{change.label}: </span>
              <span className="pd-before">{change.before ?? "Blank"}</span>
              <Icon name="next" />
              <strong className="pd-after">{change.after ?? "Blank"}</strong>
            </li>
          ))}
        </ul>
        {review.identity_changed ? (
          <p className="pd-warn">
            <Glyph name="info" />
            Earlier send and lookup approvals will need a new review.
          </p>
        ) : null}
        {review.duplicates.length ? (
          <div className="pd-duplicates">
            {review.duplicates.map((duplicate) => (
              <p key={duplicate.person_id}>
                <Glyph name="user" />
                <span>
                  Possible duplicate:{" "}
                  <a href={duplicate.href}>
                    {duplicate.name}
                    {duplicate.company ? ` · ${duplicate.company}` : ""}
                  </a>{" "}
                  (same{" "}
                  {duplicate.matched_fields
                    .map((f) => meta(f).label.toLowerCase())
                    .join(", ")}
                  )
                </span>
              </p>
            ))}
            <label className="pd-check">
              <input
                type="checkbox"
                checked={confirmed}
                disabled={busy}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              I checked the existing file. Keep this correction here.
            </label>
          </div>
        ) : null}
        {!undoTarget ? (
          <input
            className="pd-reason"
            aria-label="Reason"
            placeholder="Why (optional)"
            maxLength={500}
            value={draft.reason}
            readOnly={busy}
            onChange={(e) => {
              setDraft({ ...draft, reason: e.target.value });
              operation.current = null;
            }}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              e.preventDefault();
              void save();
            }}
          />
        ) : null}
        <div className="pd-actions">
          <button
            type="button"
            className="pd-btn is-primary"
            aria-label={undoTarget ? "Confirm Undo" : "Save correction"}
            disabled={
              busy || Boolean(review.duplicates.length && !confirmed) || stale
            }
            onClick={() => void save()}
          >
            <Icon name="check" />
            {busy ? "Saving…" : undoTarget ? "Confirm Undo" : "Save"}
          </button>
          {!undoTarget ? (
            <button
              type="button"
              className="pd-btn"
              aria-label="Back to editing"
              disabled={busy}
              onClick={closeReview}
            >
              <Icon name="edit" />
              Edit
            </button>
          ) : null}
          <button
            type="button"
            className="pd-btn"
            disabled={busy}
            onClick={cancel}
          >
            <Icon name="close" />
            Cancel
          </button>
        </div>
      </div>
    ) : null;

  const savedNames = saved?.fields.map((f) => meta(f).label) ?? [];
  return (
    <section
      ref={editor}
      className="person-details-editor"
      aria-label="Person details"
      aria-busy={busy || undefined}
      data-unsaved={dirty ? "true" : undefined}
      data-correction-busy={busy ? "true" : undefined}
    >
      <div className="pf-side-heading">
        <h2>Details</h2>
        {protectedFields ? (
          <span
            className="pf-badge"
            title="Human corrections are protected from provider updates."
          >
            Human-edited
          </span>
        ) : null}
        {/* Without an outside trigger (the Contacts preview), edit from here. */}
        {!editTrigger && draft?.scope !== "all" ? (
          <button
            type="button"
            ref={editButton}
            className="pf-icon-btn pf-side-edit"
            aria-label="Edit details"
            title="Edit details"
            disabled={busy}
            onClick={() => open("all")}
          >
            <Icon name="edit" />
          </button>
        ) : null}
      </div>
      {draft?.scope === "all" ? (
        <form
          className="pd-form"
          onSubmit={(e) => {
            e.preventDefault();
            propose();
          }}
          onKeyDown={(e) => {
            if (e.key !== "Escape" || review) return;
            e.preventDefault();
            e.stopPropagation();
            cancel();
          }}
        >
          <div className="pd-row pd-name-row">
            <Glyph name="user" />
            {input("first_name", false)}
            {input("last_name", false)}
          </div>
          {ATTRIBUTES.map(({ field }) => (
            <div key={field} className="pd-row">
              <Mark field={field} />
              {input(field, false)}
            </div>
          ))}
          {reviewCard ?? (
            <div className="pd-actions">
              <button
                type="submit"
                className="pd-btn is-primary"
                disabled={busy || !Object.keys(changes(draft)).length}
              >
                <Icon name="check" />
                {busy
                  ? "Saving…"
                  : Object.keys(changes(draft)).some((f) =>
                        REVIEWED.has(f as PersonDetailField),
                      )
                    ? "Review changes"
                    : "Save"}
              </button>
              <button
                type="button"
                className="pd-btn"
                disabled={busy}
                onClick={cancel}
              >
                <Icon name="close" />
                Cancel
              </button>
            </div>
          )}
        </form>
      ) : (
        <ul className="pf-attrs">
          {ATTRIBUTES.map(({ field, noun }) => {
            if (draft?.scope === field)
              return (
                <li key={field} className="pf-attr is-editing">
                  <Mark field={field} />
                  {input(field, true)}
                  {review ? null : (
                    <span className="pd-row-actions">
                      <button
                        type="button"
                        className="pf-icon-btn"
                        aria-label={`Save ${noun}`}
                        title="Save (Enter)"
                        disabled={busy}
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => propose()}
                      >
                        <Icon name="check" />
                      </button>
                      <button
                        type="button"
                        className="pf-icon-btn"
                        aria-label="Cancel"
                        title="Cancel (Esc)"
                        disabled={busy}
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={cancel}
                      >
                        <Icon name="close" />
                      </button>
                    </span>
                  )}
                  {reviewCard}
                </li>
              );
            const value = savedValue(field);
            const fresh = saved?.fields.includes(field) ? " is-saved" : "";
            if (!value)
              return (
                <li key={field} className={`pf-attr is-missing${fresh}`}>
                  <button
                    type="button"
                    className="pf-attr-add"
                    data-edit-field={field}
                    disabled={busy}
                    onClick={() => open(field)}
                  >
                    <Mark field={field} />
                    Add {noun}
                  </button>
                </li>
              );
            const shown = shownValue(field, value);
            return (
              <li key={field} className={`pf-attr${fresh}`}>
                <Mark field={field} />
                <button
                  type="button"
                  className="pd-value"
                  data-edit-field={field}
                  aria-label={`Edit ${noun}: ${shown}`}
                  title={`Edit ${noun}`}
                  disabled={busy}
                  onClick={() => open(field)}
                >
                  <span>{shown}</span>
                  <Icon name="edit" />
                </button>
                {field === "email" ? (
                  <CopyValue value={value} noun="email" />
                ) : field === "linkedin_url" &&
                  /^https:\/\/[^\s]+$/.test(value) ? (
                  <a
                    className="pf-icon-btn pd-hover-action"
                    href={value}
                    target="_blank"
                    rel="noreferrer"
                    aria-label="Open LinkedIn profile"
                    title="Open LinkedIn profile"
                  >
                    <Icon name="external" />
                  </a>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {saved ? (
        <p className="pd-result" role="status">
          <Icon name="check" />
          {saved.undone
            ? "Restored"
            : savedNames.length === 1
              ? `${savedNames[0]} saved`
              : "Details saved"}
          {saved.changeId ? (
            <button
              type="button"
              className="pd-btn is-quiet"
              aria-label="Undo correction"
              disabled={busy}
              onClick={() => void undo(saved.changeId!)}
            >
              <Icon name="reply" />
              Undo
            </button>
          ) : null}
        </p>
      ) : null}
      {!draft && conflicts.length ? (
        <div className="pd-conflicts">
          <h3>Provider differences</h3>
          {conflicts.map((conflict) => (
            <div key={conflict.conflict_id} className="pd-conflict">
              <Mark field={conflict.field} />
              <span>
                <span className="pf-visually-hidden">
                  {meta(conflict.field).label}:{" "}
                </span>
                <span className="pd-before">
                  {conflict.saved_value ?? "Blank"}
                </span>
                <Icon name="next" />
                <strong>{conflict.provider_value ?? "Blank"}</strong>
              </span>
              <button
                type="button"
                className="pd-btn"
                aria-label={`Review ${meta(conflict.field).label.toLowerCase()} suggestion`}
                disabled={busy}
                onClick={() => reviewConflict(conflict)}
              >
                <Icon name="edit" />
                Review
              </button>
            </div>
          ))}
        </div>
      ) : null}
      {!draft &&
      history.some((change) => change.entity_type === "person_details") ? (
        <details className="person-details-history">
          <summary>
            <Glyph name="clock" />
            Correction history
          </summary>
          <ol>
            {history
              .filter((change) => change.entity_type === "person_details")
              .map((change) => (
                <li key={change.change_id}>
                  <span>
                    {change.reason} ·{" "}
                    {change.actor === "assistant" ? "Assistant" : "You"}
                  </span>
                  {change.can_undo && change.action !== "undo" ? (
                    <button
                      type="button"
                      className="pd-btn is-quiet"
                      disabled={busy}
                      aria-label={`Undo correction: ${change.reason}`}
                      onClick={() => void undo(change.change_id)}
                    >
                      <Icon name="reply" />
                      Undo
                    </button>
                  ) : null}
                </li>
              ))}
          </ol>
        </details>
      ) : null}
      {error ? (
        <p role="alert" className="pd-error">
          {error}
          {draft ? " Your edits are kept." : ""}
        </p>
      ) : null}
      {draft && stale ? (
        <button
          type="button"
          className="pd-btn"
          disabled={busy}
          onClick={() => void reload()}
        >
          <Icon name="refresh" />
          Review latest details
        </button>
      ) : null}
    </section>
  );
});
