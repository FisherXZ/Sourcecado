import type { LlmMessage } from "@/lib/llm/types";

export interface ResumedExchange {
  question: string;
  answer: string;
}

// Folds a session's persisted transcript into display-only {question,
// answer} pairs. Deliberately does not reconstruct the reasoning trace
// (ChatStep[]) or run meta (ChatMeta) — chat_messages doesn't store either,
// and R6 is a minimal resume, not a trace replay (see plan Judgment call 4).
export function mapMessagesToResumedExchanges(messages: LlmMessage[]): ResumedExchange[] {
  const exchanges: ResumedExchange[] = [];
  let current: { question: string; answerParts: string[] } | null = null;

  for (const message of messages) {
    if (message.role === "system") continue;

    if (message.role === "user") {
      if (current) exchanges.push({ question: current.question, answer: current.answerParts.join(" ") });
      current = { question: message.content, answerParts: [] };
      continue;
    }

    if (!current) continue; // orphaned assistant/tool_result with no preceding question

    if (message.role === "assistant") {
      for (const block of message.content) {
        if (block.type === "text" && block.text) current.answerParts.push(block.text);
      }
    }
    // tool_result messages contribute nothing to the displayed answer text.
  }

  if (current) exchanges.push({ question: current.question, answer: current.answerParts.join(" ") });
  return exchanges;
}
