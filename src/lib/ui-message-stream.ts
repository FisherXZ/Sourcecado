import { createUIMessageStream, createUIMessageStreamResponse } from "ai";

// Boundary module for the AI SDK UI-message-stream transport. All `from "ai"`
// usage for streaming lives here (model-boundary.test.ts allows this file and
// model-gateway.ts) so AI SDK surface stays contained and auditable. Routes call
// streamAgentResponse and never touch the SDK directly.

export interface AgentStreamWriter {
  // A reasoning step (reconciled by id). data is the rendered ChatStepPart.
  step: (id: string, data: unknown) => void;
  // A tool has just been dispatched and is running; the reasoning trace shows
  // its name in the live pending row until the matching data-step settles.
  toolPending: (tool: string) => void;
  // One incremental chunk of the assistant's own generated text. Lazily opens
  // the "answer" text part on first use.
  answerDelta: (delta: string) => void;
  // Closes the "answer" text part with no further content — used when every
  // token was already streamed live via answerDelta and needs no correction.
  answerEnd: () => void;
  // Appends `text` as one more delta (starting the part fresh if nothing has
  // streamed yet) and closes it. Safe to call whether or not answerDelta ran
  // first — this is the authoritative, citation-checked flush.
  answerFlush: (text: string) => void;
  // Run metadata (runId, status, steps, invalidCitations).
  meta: (data: unknown) => void;
}

export function streamAgentResponse(run: (writer: AgentStreamWriter) => Promise<void>): Response {
  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      writer.write({ type: "start" });
      let answerStarted = false;
      const ensureStarted = () => {
        if (!answerStarted) {
          writer.write({ type: "text-start", id: "answer" });
          answerStarted = true;
        }
      };
      await run({
        step: (id, data) => writer.write({ type: "data-step", id, data }),
        toolPending: (tool) => writer.write({ type: "data-tool-pending", id: "tool-pending", data: { tool } }),
        answerDelta: (delta) => {
          ensureStarted();
          writer.write({ type: "text-delta", id: "answer", delta });
        },
        answerEnd: () => {
          if (answerStarted) {
            writer.write({ type: "text-end", id: "answer" });
            answerStarted = false;
          }
        },
        answerFlush: (text) => {
          ensureStarted();
          writer.write({ type: "text-delta", id: "answer", delta: text });
          writer.write({ type: "text-end", id: "answer" });
          answerStarted = false;
        },
        meta: (data) => writer.write({ type: "data-meta", id: "meta", data }),
      });
    },
    onError: (error) => (error instanceof Error ? error.message : String(error)),
  });
  return createUIMessageStreamResponse({ stream });
}
