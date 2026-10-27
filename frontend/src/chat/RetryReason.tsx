/**
 * The visible reason a Retry is off, for a person who cannot hover: one short
 * line beside the disabled button. The button's accessible name already says
 * the same thing, so this line stays out of the accessibility tree.
 */
export function RetryReason({ reason }: { readonly reason: string }) {
  return (
    <p className="sourcecado-retry-reason" aria-hidden="true">
      <strong>Retry is off.</strong> {reason}
    </p>
  );
}
