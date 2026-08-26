import { auth } from "@/auth";
import type { MemoryActor } from "../memory/actor";

export class UnauthenticatedError extends Error {
  constructor() {
    super("not authenticated");
    this.name = "UnauthenticatedError";
  }
}

// The one place a signed-in session becomes a MemoryActor. Every server
// component and route handler that touches memory, sessions, or the ledger
// calls this instead of reaching for DEFAULT_ACTOR.
//
// actorType is "user" — already permitted by the source_permissions
// principal_type CHECK (src/migrations/002_memory.sql), so grants issued to a
// real director validate without a schema change.
//
// Throws rather than falling back to a default actor: middleware should have
// rejected the request long before this runs, so reaching here signed-out means
// the matcher is wrong. Failing closed turns that into a 500 instead of
// silently serving one director's memory to another.
export async function requireActor(): Promise<MemoryActor> {
  const session = await auth();
  const userId = session?.user?.id;
  if (!userId) throw new UnauthenticatedError();
  return { actorType: "user", actorId: userId };
}

// Display identity for the app shell. Null when signed out.
export async function currentUser(): Promise<{ name: string; email: string } | null> {
  const session = await auth();
  if (!session?.user?.id) return null;
  return {
    name: session.user.name ?? session.user.email ?? "Director",
    email: session.user.email ?? "",
  };
}
