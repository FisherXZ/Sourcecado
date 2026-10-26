export type AppRoute =
  | { kind: "board" }
  | { kind: "person"; personId: string; followUpTaskId?: string }
  | { kind: "connections"; connectorId?: string }
  | { kind: "scheduled"; jobId?: number }
  | { kind: "settings" }
  | { kind: "skills" }
  | { kind: "memory" }
  | { kind: "quarantine" }
  | { kind: "chat"; sessionId?: string; personId?: string };

export function parseHash(hash: string): AppRoute {
  if (hash === "#/board") return { kind: "board" };
  if (hash.startsWith("#/people/") && hash.includes("/follow-up/")) {
    const [personId, taskId] = hash.slice("#/people/".length).split("/follow-up/", 2);
    if (personId && taskId) {
      try {
        return {
          kind: "person",
          personId: decodeURIComponent(personId),
          followUpTaskId: decodeURIComponent(taskId),
        };
      } catch {
        return { kind: "board" };
      }
    }
  }
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
  if (hash.startsWith("#/scheduled/")) {
    const rawJobId = hash.slice("#/scheduled/".length);
    const jobId = Number(rawJobId);
    if (/^\d+$/.test(rawJobId) && Number.isSafeInteger(jobId) && jobId > 0) {
      return { kind: "scheduled", jobId };
    }
    return { kind: "scheduled" };
  }
  if (hash === "#/settings") return { kind: "settings" };
  if (hash === "#/skills") return { kind: "skills" };
  if (hash === "#/memory") return { kind: "memory" };
  if (hash === "#/quarantine") return { kind: "quarantine" };
  if (hash.startsWith("#/chat/") && hash.includes("/person/")) {
    const [sessionId, personId] = hash.slice("#/chat/".length).split("/person/", 2);
    if (sessionId && personId) {
      try {
        return {
          kind: "chat",
          sessionId: decodeURIComponent(sessionId),
          personId: decodeURIComponent(personId),
        };
      } catch {
        return { kind: "chat" };
      }
    }
  }
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

/** Link to a person file with the follow-up editor open for one task. */
export function followUpHash(personId: string, taskId: string): string {
  return `#/people/${encodeURIComponent(personId)}/follow-up/${encodeURIComponent(taskId)}`;
}
