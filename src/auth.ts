import NextAuth from "next-auth";
import { authConfig } from "./auth.config";
import { getDb } from "./lib/db";
import { upsertUser } from "./lib/auth/users";

// Node-runtime Auth.js instance: the edge-safe config plus the DB-backed `jwt`
// callback. Server components and route handlers import `auth` from here;
// middleware imports the edge half directly.
export const { handlers, signIn, signOut, auth } = NextAuth({
  ...authConfig,
  callbacks: {
    ...authConfig.callbacks,

    // Google sets email_verified=false for some Workspace-delegated accounts.
    // Refuse those rather than minting a user row keyed to an address the
    // holder may not control.
    signIn({ profile }) {
      return Boolean(profile?.email && profile.email_verified);
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
