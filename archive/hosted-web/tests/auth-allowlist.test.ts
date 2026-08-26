import { describe, expect, it } from "vitest";
import { isAllowedEmail } from "@/lib/auth/allowlist";

// Env is injected rather than mutated via process.env so these cases stay
// order-independent under the single-worker runner.
const dev = { nodeEnv: "development" };
const prod = { nodeEnv: "production" };

describe("isAllowedEmail", () => {
  it("allows a listed domain", () => {
    expect(isAllowedEmail("director@codeology.org", { ...prod, allowedDomains: "codeology.org" })).toBe(true);
  });

  it("rejects an unlisted domain", () => {
    expect(isAllowedEmail("stranger@gmail.com", { ...prod, allowedDomains: "codeology.org" })).toBe(false);
  });

  it("allows an explicitly listed address whose domain is not listed", () => {
    expect(
      isAllowedEmail("fisher@gmail.com", {
        ...prod,
        allowedDomains: "codeology.org",
        allowedEmails: "fisher@gmail.com",
      })
    ).toBe(true);
  });

  it("is case- and whitespace-insensitive", () => {
    expect(isAllowedEmail("  Director@Codeology.ORG ", { ...prod, allowedDomains: " codeology.org , x.com " })).toBe(
      true
    );
  });

  // A subdomain is a different domain: allowing codeology.org must not admit
  // evil-codeology.org or mail.codeology.org.
  it("does not treat a suffix match as a domain match", () => {
    expect(isAllowedEmail("a@evil-codeology.org", { ...prod, allowedDomains: "codeology.org" })).toBe(false);
    expect(isAllowedEmail("a@mail.codeology.org", { ...prod, allowedDomains: "codeology.org" })).toBe(false);
  });

  // A quoted local-part may contain "@", so the domain is the LAST segment.
  // Splitting on the first "@" would read "b@codeology.org" as the domain of
  // `"a@b"@evil.com` and wrongly admit it.
  it("reads the domain from the last @, not the first", () => {
    expect(isAllowedEmail('"a@codeology.org"@evil.com', { ...prod, allowedDomains: "codeology.org" })).toBe(false);
  });

  describe("with nothing configured", () => {
    it("allows any verified account in development", () => {
      expect(isAllowedEmail("anyone@gmail.com", dev)).toBe(true);
    });

    // Fail closed: an unconfigured production deployment must not be an open
    // sign-up for anyone with a Google account.
    it("denies everyone in production", () => {
      expect(isAllowedEmail("anyone@gmail.com", prod)).toBe(false);
    });
  });

  it("rejects an empty address", () => {
    expect(isAllowedEmail("", { ...dev })).toBe(false);
    expect(isAllowedEmail("   ", { ...dev })).toBe(false);
  });
});
