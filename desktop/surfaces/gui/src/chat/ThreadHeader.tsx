export function ThreadHeader({
  title,
  personaName,
}: {
  readonly title: string | null;
  readonly personaName?: string | null;
}) {
  return (
    <header className="sourcecado-thread-header">
      <div>
        <p className="eyebrow">Sourcing workspace</p>
        <h1>{title?.trim() || "New sourcing conversation"}</h1>
      </div>
      {personaName ? (
        <p className="sourcecado-persona-badge">{personaName}</p>
      ) : null}
    </header>
  );
}
