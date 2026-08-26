import NextAuth from "next-auth";
import { authConfig } from "@/auth.config";

// The single authentication chokepoint. Instantiated from the edge-safe config
// (no DB import) and gated by the `authorized` callback, which redirects
// signed-out requests to /login.
//
// Deny-by-default: the matcher below protects every path except the explicit
// exclusions, so a new page or API route added later is protected the moment it
// exists. Before this file, /api/agent, /api/memory/note, /api/memory/sources,
// and /api/memory/import were reachable unauthenticated.
export const { auth: middleware } = NextAuth(authConfig);

export default middleware;

export const config = {
  matcher: [
    // Excluded: the Auth.js endpoints themselves (sign-in would loop), the
    // unauthenticated health probe, the login page, and Next's static assets.
    //
    // Each exclusion is anchored with (?:/|$) rather than left as a bare
    // prefix. Unanchored, "login" also excludes /loginfoo and "api/health"
    // also excludes /api/healthcheck — so any future route whose name merely
    // starts with an exclusion would silently skip authentication.
    "/((?!api/auth(?:/|$)|api/health(?:/|$)|login(?:/|$)|_next/static/|_next/image/|favicon\\.ico$).*)",
  ],
};
