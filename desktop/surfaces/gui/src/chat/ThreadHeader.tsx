export function ThreadHeader({ title }: { readonly title: string | null }) {
  return (
    <header className="sourcecado-thread-header">
      <div>
        <p className="eyebrow">Sourcing workspace</p>
        <h1>{title?.trim() || "New sourcing conversation"}</h1>
      </div>
    </header>
  );
}
