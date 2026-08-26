export type AppRoute =
  | { kind: "board" }
  | { kind: "person"; personId: string }
  | { kind: "connections"; connectorId?: string }
  | { kind: "scheduled" }
  | { kind: "settings" }
  | { kind: "skills" }
  | { kind: "chat"; sessionId?: string };

export function parseHash(hash: string): AppRoute {
  if (hash === "#/board") return { kind: "board" };
  if (hash.startsWith("#/people/") && hash.length > "#/people/".length) {
    try {
      return {
        kind: "person",
        personId: decodeURIComponent(hash.slice("#/people/".length)),
      };
    } catch {
      return { kind: "board" };
    }
  }
  if (hash === "#/connections") return { kind: "connections" };
  if (hash.startsWith("#/connections/") && hash.length > "#/connections/".length) {
    try {
      return {
        kind: "connections",
        connectorId: decodeURIComponent(hash.slice("#/connections/".length)),
      };
    } catch {
      return { kind: "chat" };
    }
  }
  if (hash === "#/scheduled") return { kind: "scheduled" };
  if (hash === "#/settings") return { kind: "settings" };
  if (hash === "#/skills") return { kind: "skills" };
  if (hash.startsWith("#/chat/") && hash.length > "#/chat/".length) {
    try {
      return {
        kind: "chat",
        sessionId: decodeURIComponent(hash.slice("#/chat/".length)),
      };
    } catch {
      return { kind: "chat" };
    }
  }
  return { kind: "chat" };
}
