import type { DefaultSession } from "next-auth";

// `id` is the internal users.id (see src/migrations/007_users_auth.sql), not
// Google's `sub`. It is what becomes MemoryActor.actorId.
declare module "next-auth" {
  interface Session {
    user: { id: string } & DefaultSession["user"];
  }
}

// Augments "@auth/core/jwt", not "next-auth/jwt": the latter is a bare
// `export * from "@auth/core/jwt"`, so declaring against it creates a separate
// JWT interface instead of merging with the real one.
declare module "@auth/core/jwt" {
  interface JWT {
    userId?: string;
  }
}
