export interface MemoryActor {
  actorType: "user" | "oauth_client" | "test_client";
  actorId: string;
}

export const DEFAULT_ACTOR: MemoryActor = {
  actorType: "test_client",
  actorId: "default",
};
