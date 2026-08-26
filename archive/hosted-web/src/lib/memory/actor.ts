export interface MemoryActor {
  actorType: "user" | "oauth_client" | "test_client";
  actorId: string;
}

// Pre-H1 single-tenant sentinel. No web entry point uses it any more — those
// resolve a real `user` actor via requireActor() (src/lib/auth/actor.ts). It
// survives as the default for CLI scripts and tests, which have no session.
export const DEFAULT_ACTOR: MemoryActor = {
  actorType: "test_client",
  actorId: "default",
};
