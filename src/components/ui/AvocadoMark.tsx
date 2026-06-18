export function AvocadoMark({ size = 56 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <ellipse cx="12" cy="13" rx="7.5" ry="9.5" fill="var(--accent)" />
      <ellipse cx="12" cy="14.5" rx="3.3" ry="3.8" fill="var(--pit)" />
      <circle cx="9.6" cy="9" r="0.9" fill="#fff" opacity="0.85" />
    </svg>
  );
}
