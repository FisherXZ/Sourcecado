// Client-side reader for the /api/agent/stream UI-message-stream (AI SDK v6 SSE).
// We own the multi-turn state, so we reduce the AI SDK chunks into a small view
// model instead of pulling in the React binding.

export interface ChatStep {
  index: number;
  tool: string;
  thought?: string;
  ok: boolean;
  detail: string;
  // Mirrors ChatStepPart's structured fields (src/lib/memory/answer.ts) —
  // present only for their respective tool (get_contact/list_outreach_history/
  // search_memory) and only on a successful, non-ambiguous result.
  contactCard?: ContactCardData;
  outreachHistory?: OutreachHistoryEntry[];
  memoryFacts?: MemoryFactEntry[];
}

export interface ContactCardData {
  id: number;
  canonicalName: string;
  role: string | null;
  organizationName: string | null;
}

export interface OutreachHistoryEntry {
  occurredAt: string;
  channel: string | null;
  summary: string;
  citation: string | null;
}

export interface MemoryFactEntry {
  subject: string;
  predicate: string;
  object: string;
  citation: string | null;
  status: string;
}

// A Contact Profile Card's full view model, assembled from up to three
// separate steps within the same turn: the get_contact step that resolved
// the person, plus whichever list_outreach_history / search_memory steps
// also ran in that turn. This is a same-turn heuristic (there is no
// contactId tag linking a search_memory step to a specific get_contact
// step) — sound as long as the agent's doctrine is "resolve the person
// first, then look up their history/facts," which is what B1.7 establishes.
export interface ContactCardView {
  contact: ContactCardData;
  history: OutreachHistoryEntry[];
  acceptedFacts: MemoryFactEntry[];
  gapFacts: MemoryFactEntry[];
}

// Picks the most recent contactCard-bearing step, if any, and merges in the
// most recent outreachHistory/memoryFacts from the same list of steps.
// Returns undefined when no get_contact step resolved to a single contact
// in this turn — the normal case for a non-person question.
export function selectContactCard(steps: ChatStep[]): ContactCardView | undefined {
  const contactStep = [...steps].reverse().find((s) => s.contactCard);
  if (!contactStep?.contactCard) return undefined;

  const historyStep = [...steps].reverse().find((s) => s.outreachHistory);
  const factsStep = [...steps].reverse().find((s) => s.memoryFacts);
  const facts = factsStep?.memoryFacts ?? [];

  return {
    contact: contactStep.contactCard,
    history: historyStep?.outreachHistory ?? [],
    acceptedFacts: facts.filter((f) => f.status === "accepted"),
    gapFacts: facts.filter((f) => f.status !== "accepted"),
  };
}

export interface ChatMeta {
  runId: number;
  status: "succeeded" | "failed";
  steps: number;
  invalidCitations: string[];
}

export interface AssistantTurn {
  steps: ChatStep[];
  answer: string;
  meta?: ChatMeta;
  // Name of the tool currently dispatched but not yet settled into a ChatStep,
  // shown in the reasoning trace's live pending row. Cleared once the matching
  // data-step or the run's data-meta lands.
  pendingTool?: string;
}

export interface ConversationTurn {
  role: "user" | "assistant";
  content: string;
}

// A decoded UI-message-stream chunk. Only the fields we consume are typed.
export interface UiChunk {
  type: string;
  delta?: string;
  data?: unknown;
}

// Pull every complete `data:` SSE event out of a rolling buffer, returning the
// parsed chunks and whatever incomplete tail is left for the next read.
export function drainSse(buffer: string): { chunks: UiChunk[]; rest: string } {
  const chunks: UiChunk[] = [];
  let rest = buffer;
  let sep = rest.indexOf("\n\n");
  while (sep !== -1) {
    const rawEvent = rest.slice(0, sep);
    rest = rest.slice(sep + 2);
    for (const line of rawEvent.split("\n")) {
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload || payload === "[DONE]") continue;
      try {
        chunks.push(JSON.parse(payload) as UiChunk);
      } catch {
        // Ignore non-JSON keep-alives / partial lines.
      }
    }
    sep = rest.indexOf("\n\n");
  }
  return { chunks, rest };
}

// Fold one chunk into the assistant turn. Pure and immutable so the React state
// update is a straight replacement.
export function applyChunk(turn: AssistantTurn, chunk: UiChunk): AssistantTurn {
  switch (chunk.type) {
    case "data-step": {
      const step = chunk.data as ChatStep;
      const exists = turn.steps.some((s) => s.index === step.index);
      const steps = exists
        ? turn.steps.map((s) => (s.index === step.index ? step : s))
        : [...turn.steps, step];
      return { ...turn, steps, pendingTool: undefined };
    }
    case "data-tool-pending":
      return { ...turn, pendingTool: (chunk.data as { tool: string }).tool };
    case "text-delta":
      return { ...turn, answer: turn.answer + (chunk.delta ?? "") };
    case "data-meta":
      return { ...turn, meta: chunk.data as ChatMeta, pendingTool: undefined };
    default:
      return turn;
  }
}

// Stream one agent turn. Calls onUpdate with the accumulating assistant turn each
// time new chunks land, so the UI can render the reasoning trace live.
export async function runChat(
  question: string,
  history: ConversationTurn[],
  onUpdate: (turn: AssistantTurn) => void,
  signal?: AbortSignal
): Promise<AssistantTurn> {
  const res = await fetch("/api/agent/stream", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, history }),
    signal,
  });
  if (!res.ok) {
    throw new Error(`Stream failed (${res.status})`);
  }
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let turn: AssistantTurn = { steps: [], answer: "" };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { chunks, rest } = drainSse(buffer);
    buffer = rest;
    if (chunks.length) {
      for (const chunk of chunks) turn = applyChunk(turn, chunk);
      onUpdate(turn);
    }
  }
  return turn;
}
