import NextAuth from "next-auth";
import { authConfig } from "./auth.config";
import { getDb } from "./lib/db";
import { upsertUser } from "./lib/auth/users";
import { isAllowedEmail } from "./lib/auth/allowlist";

// Node-runtime Auth.js instance: the edge-safe config plus the DB-backed `jwt`
// callback. Server components and route handlers import `auth` from here;
// middleware imports the edge half directly.
export const { handlers, signIn, signOut, auth } = NextAuth({
  ...authConfig,
  callbacks: {
    ...authConfig.callbacks,

    // Two separate questions, both of which must pass:
    //   1. does this person control the mailbox (email_verified)? Google sets
    //      it false for some Workspace-delegated accounts.
    //   2. do they belong here (isAllowedEmail)? Verification alone would let
    //      any Gmail account reach chat and memory — and spend Apollo credits
    //      and model tokens on our keys.
    signIn({ profile }) {
      if (!profile?.email || !profile.email_verified) return false;
      return isAllowedEmail(profile.email);
    },

    // Runs only on sign-in (`profile` is undefined on subsequent token
    // refreshes), so this is one upsert per login, not per request. Every
    // later request reads users.id straight off the signed cookie.
    async jwt({ token, profile }) {
      if (profile?.sub && profile.email) {
        const user = await upsertUser(getDb(), {
          googleSub: profile.sub,
          email: profile.email,
          name: profile.name ?? null,
          imageUrl: typeof profile.picture === "string" ? profile.picture : null,
        });
        token.userId = String(user.id);
      }
      return token;
    },
  },
});
