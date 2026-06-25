export interface MemoryActor {
  actorType: "user" | "oauth_client" | "test_client";
  actorId: string;
}

// v1 single-tenant sentinel. Replace with a real actor when per-user auth ships.
export const DEFAULT_ACTOR: MemoryActor = {
  actorType: "test_client",
  actorId: "default",
};
