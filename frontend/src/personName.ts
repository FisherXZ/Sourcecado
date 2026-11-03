export function hasApolloNameMask(value: unknown): boolean {
  return typeof value === "string" && value.includes("*");
}

export function withoutApolloNameMasks(value: string): string {
  return value
    .split(/\s+/)
    .map((token) => {
      const core = token.replace(/^["'“”‘’()[\]{}.,!?;:]+|["'“”‘’()[\]{}.,!?;:]+$/gu, "");
      const markdownEmphasis = core.startsWith("*") && core.endsWith("*");
      const hasUnicodeLetterOrNumber = /[\p{L}\p{N}]/u.test(core.replaceAll("*", ""));
      return token.includes("*") && hasUnicodeLetterOrNumber && !markdownEmphasis
        ? "(surname hidden by Apollo)"
        : token;
    })
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

/** Up to two initials for an avatar; "?" when no name is saved. */
export function personInitials(person: { first_name?: string | null; last_name?: string | null }): string {
  const letters = [person.first_name, person.last_name]
    .filter((value): value is string => typeof value === "string" && value.length > 0)
    .map((value) => Array.from(value)[0])
    .join("")
    .slice(0, 2);
  return letters.toLocaleUpperCase() || "?";
}
