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
