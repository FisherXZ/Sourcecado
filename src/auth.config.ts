import type { NextAuthConfig } from "next-auth";
import Google from "next-auth/providers/google";

// Edge-safe half of the Auth.js config: no database import, no Node built-ins.
// `middleware.ts` instantiates NextAuth from this alone so route protection can
// run at the edge, while `src/auth.ts` layers the DB-backed `jwt` callback on
// top for the Node runtime. Splitting is required, not stylistic — importing
// `postgres` into middleware fails to build.
//
// Scopes are login-only (openid/email/profile, the Google provider default).
// Gmail draft scopes get added when the Gmail ticket lands; widening later only
// costs a re-consent, whereas asking for Gmail access at first login would put
// an unverified-app warning in front of every director for a feature that does
// not exist yet.
export const authConfig = {
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    }),
  ],
  session: { strategy: "jwt" },
  pages: { signIn: "/login" },
  callbacks: {
    // Consulted by middleware for every matched request.
    authorized({ auth }) {
      return Boolean(auth?.user);
    },
    // Surfaces the internal users.id (stamped onto the token in src/auth.ts)
    // as session.user.id. Reads the token only, so it is edge-safe.
    session({ session, token }) {
      if (token.userId && session.user) {
        session.user.id = token.userId;
      }
      return session;
    },
  },
} satisfies NextAuthConfig;
