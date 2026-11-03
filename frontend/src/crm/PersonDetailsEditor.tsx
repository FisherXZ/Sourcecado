import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
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
type Draft = {
  version: number;
  values: Record<PersonDetailField, string>;
  changed: PersonDetailField[];
  reason: string;
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
      ? raw
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

function safeLink(field: PersonDetailField, value: string): string | null {
  if (field === "email") return `mailto:${value}`;
  if (field === "phone") return `tel:${value.replace(/[^\d+]/g, "")}`;
  if (field === "linkedin_url")
    return /^https:\/\/[^\s]+$/.test(value) ? value : null;
  return null;
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
      className="pf-icon-btn"
      aria-label={label}
      title={label}
      onClick={() =>
        void navigator.clipboard
          ?.writeText(value)
          .then(() => setCopied(true))
          .catch(() => {
            /* The value stays selectable as a link. */
          })
      }
    >
      <Icon name={copied ? "approve" : "copy"} />
    </button>
  );
}

export type PersonDetailsHandle = {
  /** Open the editor, optionally focused on one field. Keeps an open draft. */
  begin: (field?: PersonDetailField) => void;
};

/** One correction form serves the compact preview and durable Person File. */
export const PersonDetailsEditor = forwardRef<
  PersonDetailsHandle,
  {
    person: PersonFile["person"];
    onSaved: (person: PersonFile["person"]) => void;
    history?: CrmChange[];
    onBusyChange?: (busy: boolean) => void;
    /** "sidebar": Person File attribute list; the edit trigger lives outside. */
    variant?: "panel" | "sidebar";
    /** Where focus returns after an edit when the trigger lives outside. */
    editTrigger?: RefObject<HTMLButtonElement | null>;
  }
>(function PersonDetailsEditor(
  {
    person,
    onSaved,
    history = [],
    onBusyChange,
    variant = "panel",
    editTrigger,
  },
  ref,
) {
  const pid = person.person_id;
  const sidebar = variant === "sidebar";
  const [draft, setDraft] = useState<Draft | null>(() => read(pid));
  const [review, setReview] = useState<PersonCorrectionReview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [undoTarget, setUndoTarget] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [sourceRefs, setSourceRefs] = useState<string[]>([]);
  const editButton = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(Boolean(draft));
  const editor = useRef<HTMLElement>(null);
  const operation = useRef<{ fingerprint: string; id: string } | null>(null);
  const focusField = useRef<PersonDetailField | null>(null);
  const focusTrigger = () => (editTrigger?.current ?? editButton.current)?.focus();
  const controls = person.identity_field_controls as
    Record<string, { protected?: boolean }> | undefined;
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
          focusField.current ? `input[name="${focusField.current}"]` : "input",
        )
        ?.focus();
    focusField.current = null;
  }, [Boolean(draft), Boolean(review)]);
  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);
  useEffect(() => {
    if (!draft && wasEditing.current) focusTrigger();
    wasEditing.current = Boolean(draft);
  }, [Boolean(draft)]);
  useImperativeHandle(ref, () => ({
    begin(field) {
      if (draft) {
        editor.current?.querySelector<HTMLInputElement>("input")?.focus();
        return;
      }
      focusField.current = field ?? null;
      begin();
    },
  }));
  function begin() {
    setUndoTarget(null);
    setDraft({
      version: person.version ?? 0,
      values: values(person),
      changed: [],
      reason: "",
    });
    setReview(null);
    setError(null);
    setStale(false);
  }
  function cancel() {
    clearPersonCorrectionDraft(pid);
    setDraft(null);
    setReview(null);
    setConfirmed(false);
    setError(null);
    setStale(false);
    setSourceRefs([]);
    setUndoTarget(null);
    operation.current = null;
    focusTrigger();
  }
  function changes(d: Draft) {
    return Object.fromEntries(
      d.changed.map((field) => [field, d.values[field].trim() || null]),
    );
  }
  async function prepare() {
    if (!draft || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await reviewPersonCorrection(pid, {
        changes: changes(draft),
        expected_version: draft.version,
      });
      setReview(result);
      setConfirmed(false);
    } catch (e) {
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
  async function save() {
    if (!draft || !review || busy || (review.duplicates.length && !confirmed))
      return;
    const input: Omit<PersonCorrectionInput, "operation_id"> = {
      changes: changes(draft),
      expected_version: draft.version,
      reason: draft.reason.trim(),
      source_refs: sourceRefs,
      ...(review.duplicates.length
        ? {
            collision_token: review.collision_token,
            confirmed_duplicate_ids: review.duplicates.map((p) => p.person_id),
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
      clearPersonCorrectionDraft(pid);
      setDraft(null);
      setReview(null);
      operation.current = null;
      setSourceRefs([]);
      onSaved(result.person);
      window.dispatchEvent(new Event("sourcecado:board-changed"));
      focusTrigger();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Couldn’t save. Your edits are kept.",
      );
      if (e instanceof PersonCorrectionError && e.review) {
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
        setDraft({
          version: person.version ?? 0,
          values: next,
          changed: e.review.changes.map((change) => change.field),
          reason: "Undo correction",
        });
        setReview(e.review);
        setConfirmed(false);
        setUndoTarget(changeId);
        operation.current = null;
      }
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      ref={editor}
      className="person-details-editor"
      aria-label="Person details"
      data-unsaved={dirty ? "true" : undefined}
      data-correction-busy={busy ? "true" : undefined}
    >
      {sidebar ? (
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
        </div>
      ) : (
        <div className="person-details-heading">
          <h3>Details</h3>
          {!draft ? (
            <button type="button" ref={editButton} onClick={begin}>
              <Icon name="edit" />
              Edit details
            </button>
          ) : null}
        </div>
      )}
      {!draft ? (
        <>
          {sidebar ? (
            <ul className="pf-attrs">
              {ATTRIBUTES.map(({ field, noun, icon }) => {
                const raw = person[field];
                const value = typeof raw === "string" ? raw.trim() : "";
                const mark =
                  "glyph" in icon ? (
                    <Glyph name={icon.glyph} />
                  ) : (
                    <Icon name={icon.icon} />
                  );
                if (!value)
                  return (
                    <li key={field} className="pf-attr is-missing">
                      <button
                        type="button"
                        className="pf-attr-add"
                        onClick={() => {
                          focusField.current = field;
                          begin();
                        }}
                      >
                        {mark}
                        Add {noun}
                      </button>
                    </li>
                  );
                const href = safeLink(field, value);
                return (
                  <li key={field} className="pf-attr">
                    {mark}
                    {href ? (
                      <a
                        href={href}
                        {...(field === "linkedin_url"
                          ? { target: "_blank", rel: "noreferrer" }
                          : {})}
                      >
                        {shownValue(field, value)}
                      </a>
                    ) : (
                      <span>{value}</span>
                    )}
                    {field === "email" ? (
                      <CopyValue value={value} noun="email" />
                    ) : null}
                  </li>
                );
              })}
            </ul>
          ) : (
            <>
              <dl className="person-details-contact">
                {["email", "phone", "linkedin_url"].map((field) => {
                  const value = person[field];
                  return typeof value === "string" && value ? (
                    <div key={field}>
                      <dt>{FIELDS.find((f) => f.field === field)?.label}</dt>
                      <dd>{value}</dd>
                    </div>
                  ) : null;
                })}
              </dl>
              {!person.email && !person.phone && !person.linkedin_url ? (
                <p className="person-empty">No contact details saved.</p>
              ) : null}
              {protectedFields ? (
                <p className="person-details-protection">
                  Human corrections are protected from provider updates.
                </p>
              ) : null}
            </>
          )}
          {conflicts.length ? (
            <div className="person-details-conflicts">
              <h4>Provider differences to review</h4>
              {conflicts.map((conflict) => (
                <article key={conflict.conflict_id}>
                  <p>
                    <strong>
                      {FIELDS.find((f) => f.field === conflict.field)?.label}
                    </strong>{" "}
                    · Saved: {conflict.saved_value ?? "Blank"}
                  </p>
                  <p>Provider: {conflict.provider_value ?? "Blank"}</p>
                  <button
                    type="button"
                    onClick={() => {
                      setDraft({
                        version: person.version ?? 0,
                        values: {
                          ...values(person),
                          [conflict.field]: conflict.provider_value ?? "",
                        },
                        changed: [conflict.field],
                        reason: "Reviewed provider suggestion",
                      });
                      setSourceRefs(
                        conflict.source_ref_id ? [conflict.source_ref_id] : [],
                      );
                      setError(null);
                    }}
                  >
                    <Icon name="edit" />
                    Review{" "}
                    {FIELDS.find(
                      (f) => f.field === conflict.field,
                    )?.label.toLowerCase()}{" "}
                    suggestion
                  </button>
                </article>
              ))}
            </div>
          ) : null}
          {history.some((change) => change.entity_type === "person_details") ? (
            <details className="person-details-history">
              <summary>Correction history</summary>
              <ol>
                {history
                  .filter((change) => change.entity_type === "person_details")
                  .map((change) => (
                    <li key={change.change_id}>
                      <p>
                        {change.reason} ·{" "}
                        {change.actor === "assistant" ? "Assistant" : "You"}
                      </p>
                      {change.can_undo && change.action !== "undo" ? (
                        <button
                          type="button"
                          disabled={busy}
                          aria-label={`Undo correction: ${change.reason}`}
                          onClick={() => void undo(change.change_id)}
                        >
                          <Icon name="refresh" />
                          Undo correction
                        </button>
                      ) : null}
                    </li>
                  ))}
              </ol>
            </details>
          ) : null}
          {error ? (
            <p role="alert" className="task-error">
              {error}
            </p>
          ) : null}
        </>
      ) : (
        <>
          {review ? (
            <div className="person-details-review">
              <h4>Review person correction</h4>
              <table aria-label="Person detail changes">
                <thead>
                  <tr>
                    <th scope="col">Detail</th>
                    <th scope="col">Saved</th>
                    <th scope="col">Correction</th>
                  </tr>
                </thead>
                <tbody>
                  {review.changes.map((change) => (
                    <tr key={change.field}>
                      <th scope="row">{change.label}</th>
                      <td>{change.before ?? "Blank"}</td>
                      <td>{change.after ?? "Blank"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {review.identity_changed ? (
                <p>
                  Earlier send and enrichment approvals will need a new review.
                </p>
              ) : null}
              {review.duplicates.length ? (
                <div className="person-details-duplicates">
                  <h4>Possible duplicate</h4>
                  {review.duplicates.map((duplicate) => (
                    <article key={duplicate.person_id}>
                      <a href={duplicate.href}>
                        {duplicate.name}
                        {duplicate.company ? ` · ${duplicate.company}` : ""}
                      </a>
                      <p>
                        Matching{" "}
                        {duplicate.matched_fields
                          .map((f) =>
                            FIELDS.find(
                              (field) => field.field === f,
                            )?.label.toLowerCase(),
                          )
                          .join(", ")}
                      </p>
                    </article>
                  ))}
                  <label>
                    <input
                      type="checkbox"
                      checked={confirmed}
                      disabled={busy}
                      onChange={(e) => setConfirmed(e.target.checked)}
                    />
                    I reviewed the existing files. Keep this correction on this
                    person.
                  </label>
                </div>
              ) : null}
              <p className="person-details-reason">
                <strong>Reason:</strong> {draft.reason}
              </p>
              <div className="person-details-actions">
                <button
                  type="button"
                  disabled={
                    busy ||
                    Boolean(review.duplicates.length && !confirmed) ||
                    stale
                  }
                  onClick={() => void save()}
                >
                  <Icon name="save" />
                  {busy
                    ? "Saving…"
                    : undoTarget
                      ? "Confirm Undo"
                      : "Save correction"}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setReview(null);
                    setError(null);
                  }}
                >
                  <Icon name="edit" />
                  Back to editing
                </button>
                <button type="button" disabled={busy} onClick={cancel}>
                  <Icon name="close" />
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void prepare();
              }}
            >
              <div className="person-details-fields">
                {FIELDS.map(({ field, label, max, type }) => (
                  <label key={field}>
                    {label}
                    <input
                      name={field}
                      type={type ?? "text"}
                      autoComplete="off"
                      maxLength={max}
                      value={draft.values[field]}
                      disabled={busy}
                      onChange={(e) => {
                        const value = e.target.value;
                        setDraft({
                          ...draft,
                          values: { ...draft.values, [field]: value },
                          changed: draft.changed.includes(field)
                            ? draft.changed
                            : [...draft.changed, field],
                        });
                        setError(null);
                        operation.current = null;
                      }}
                    />
                  </label>
                ))}
              </div>
              <label className="person-details-source">
                Reason or source
                <textarea
                  value={draft.reason}
                  maxLength={500}
                  required
                  disabled={busy}
                  onChange={(e) => {
                    setDraft({ ...draft, reason: e.target.value });
                    operation.current = null;
                  }}
                />
              </label>
              <div className="person-details-actions">
                <button
                  type="submit"
                  disabled={
                    busy || !draft.changed.length || !draft.reason.trim()
                  }
                >
                  <Icon name="approve" />
                  {busy ? "Reviewing…" : "Review changes"}
                </button>
                <button type="button" disabled={busy} onClick={cancel}>
                  <Icon name="close" />
                  Cancel
                </button>
              </div>
            </form>
          )}
          {error ? (
            <p className="task-error" role="alert">
              {error} Your edits are kept.
            </p>
          ) : null}
          {stale ? (
            <button type="button" disabled={busy} onClick={() => void reload()}>
              <Icon name="refresh" />
              Review latest details
            </button>
          ) : null}
        </>
      )}
    </section>
  );
});
