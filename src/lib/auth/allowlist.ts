// Who is allowed to hold a session at all.
//
// Google's `email_verified` only proves the person controls that mailbox — it
// says nothing about whether they belong here. Without this gate any Gmail
// account could sign in and reach chat and memory, which also spends Apollo
// credits and model tokens on our keys.
//
// Two knobs, either or both:
//   AUTH_ALLOWED_DOMAINS=codeology.org,berkeley.edu
//   AUTH_ALLOWED_EMAILS=someone@gmail.com,other@gmail.com
//
// With neither configured, this allows any verified account in development
// (so a fresh clone can sign in) but denies everything in production. Failing
// open in production is how an internal tool quietly becomes public.

function splitList(raw: string | undefined): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((entry) => entry.trim().toLowerCase())
    .filter(Boolean);
}

export function isAllowedEmail(
  email: string,
  env: { allowedDomains?: string; allowedEmails?: string; nodeEnv?: string } = {
    allowedDomains: process.env.AUTH_ALLOWED_DOMAINS,
    allowedEmails: process.env.AUTH_ALLOWED_EMAILS,
    nodeEnv: process.env.NODE_ENV,
  }
): boolean {
  const normalized = email.trim().toLowerCase();
  if (!normalized) return false;

  const domains = splitList(env.allowedDomains);
  const emails = splitList(env.allowedEmails);

  if (domains.length === 0 && emails.length === 0) {
    return env.nodeEnv !== "production";
  }

  if (emails.includes(normalized)) return true;

  // Compare against the last "@" segment: local-parts may legally contain "@"
  // when quoted, so splitting on the first one can misread the domain.
  const at = normalized.lastIndexOf("@");
  if (at === -1) return false;
  const domain = normalized.slice(at + 1);
  return domains.includes(domain);
}
