export function hasApolloNameMask(value: unknown): boolean {
  return typeof value === "string" && value.includes("*");
}

export function withoutApolloNameMasks(value: string): string {
  return value
    .split(/\s+/)
    .map((token) =>
      token.includes("*") && /[A-Za-z]/.test(token)
        ? "(surname hidden by Apollo)"
        : token,
    )
    .join(" ");
}

export function sanitizeApolloNameMasks(value: unknown): unknown {
  if (typeof value === "string") return withoutApolloNameMasks(value);
  if (Array.isArray(value)) return value.map(sanitizeApolloNameMasks);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, sanitizeApolloNameMasks(item)]),
    );
  }
  return value;
}
