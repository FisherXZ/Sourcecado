import { useEffect, useRef, useState, type FormEvent } from "react";

import { Icon } from "../icons";
import { getPerson, savePersonHandoff, type PersonFile } from "../api";

const HANDOFF_FIELDS = [
  { key: "who", label: "Who this is" },
  { key: "wanted", label: "What we wanted" },
  { key: "happened", label: "What happened" },
  { key: "theyWant", label: "What they want" },
] as const;

const STALE_HANDOFF_LABELS: Record<string, string> = {
  who: "Who this is",
  wanted: "What we wanted",
  happened: "What happened",
  they_want: "What they want",
};

type HandoffDraft = Record<(typeof HANDOFF_FIELDS)[number]["key"], string>;

/** The four-field handoff a later officer reads first, saved as a version. */
export function SuccessorHandoff({
  personId,
  file,
  onSaved,
}: {
  personId: string;
  file: PersonFile;
  onSaved: (next: PersonFile) => void;
}) {
  const [handoff, setHandoff] = useState<HandoffDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const dirty = useRef(false);
  const owner = useRef(personId);
  const brief = file.brief;

  // The form starts from what the brief already says: a saved handoff when
  // the director wrote one, otherwise the draft generated from the claims.
  // A refresh never overwrites unsaved edits to the same person.
  useEffect(() => {
    if (owner.current !== file.person.person_id) {
      owner.current = file.person.person_id;
      dirty.current = false;
    }
    if (dirty.current) return;
    const stored = file.brief.handoff;
    setHandoff({
      who: stored.who,
      wanted: stored.wanted,
      happened: stored.happened,
      theyWant: stored.they_want,
    });
  }, [file]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!handoff || busy) return;
    setBusy(true);
    setStatus(null);
    try {
      const result = await savePersonHandoff(personId, {
        ...handoff,
        expectedVersion: brief.person_version,
      });
      dirty.current = false;
      onSaved(await getPerson(personId));
      setStatus(
        result.saved ? "Saved as a new person-file version." : "No handoff changes to save.",
      );
    } catch {
      setStatus("Couldn’t save the handoff. Refresh the person file and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="person-section" aria-labelledby="person-handoff-heading">
      <div className="person-section-heading-row">
        <h2 id="person-handoff-heading" tabIndex={-1}>
          Successor handoff
        </h2>
        {brief.handoff.generated ? (
          <span
            className="pf-badge"
            title="Drafted from the brief claims. Review each field, then save it as a version."
          >
            Draft
          </span>
        ) : brief.handoff.freshness_unknown ? null : (
          <span className="pf-badge is-ok">Version {brief.handoff.version}</span>
        )}
      </div>
      {brief.handoff.freshness_unknown ? (
        <p className="person-attention">
          Saved version not recorded. Review every field.
        </p>
      ) : null}
      {brief.handoff.stale ? (
        <p className="person-attention">
          Review outdated fields:{" "}
          {brief.handoff.stale_fields
            .map((key) => STALE_HANDOFF_LABELS[key])
            .filter((label): label is string => Boolean(label))
            .map((label, index, labels) =>
              index === labels.length - 1 && labels.length > 1 ? `and ${label}` : label,
            )
            .join(brief.handoff.stale_fields.length > 2 ? ", " : " ")}
          .
        </p>
      ) : null}
      <form className="person-handoff" onSubmit={(event) => void save(event)}>
        {HANDOFF_FIELDS.map((field) => (
          <p key={field.key}>
            <label htmlFor={`person-handoff-${field.key}`}>{field.label}</label>
            <textarea
              id={`person-handoff-${field.key}`}
              rows={2}
              value={handoff?.[field.key] ?? ""}
              onChange={(event) => {
                dirty.current = true;
                setHandoff((current) =>
                  current === null ? current : { ...current, [field.key]: event.target.value },
                );
              }}
            />
          </p>
        ))}
        <button type="submit" disabled={busy}>
          <Icon name="save" />
          {busy ? "Saving…" : "Save handoff version"}
        </button>
      </form>
      {status ? <p role="status">{status}</p> : null}
    </section>
  );
}
