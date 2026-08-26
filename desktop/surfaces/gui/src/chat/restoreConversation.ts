import type { ThreadMessageLike } from "@assistant-ui/react";

import type { Conversation } from "../api";
import {
  convertLegacyTranscript,
  convertStructuredMessage,
  structureLegacyTranscript,
  type SourcecadoStructuredMessage,
} from "./messageAdapter";
import { SourcecadoChatStore } from "./store";

function textOf(message: ThreadMessageLike): string {
  if (typeof message.content === "string") return message.content;
  return message.content
    .filter((part) => part.type === "text")
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("");
}

export function restoreConversationMessages(
  conversation: Conversation,
): ThreadMessageLike[] {
  if (conversation.events.length === 0) {
    return convertLegacyTranscript(conversation.id, conversation.messages);
  }
  const legacy = convertLegacyTranscript(conversation.id, conversation.messages);
  const store = new SourcecadoChatStore(
    [{ id: conversation.id, messages: [] }],
    conversation.id,
  );
  store.replayChatEvents(conversation.events);
  const projected = store
    .messagesFor(conversation.id)
    .map(convertStructuredMessage);
  const merged = [...legacy];
  let cursor = 0;
  for (const message of projected) {
    const match = merged.findIndex(
      (candidate, index) =>
        index >= cursor &&
        candidate.role === "assistant" &&
        textOf(candidate) === textOf(message),
    );
    if (match < 0) {
      merged.push(message);
      continue;
    }
    merged[match] = message;
    cursor = match + 1;
  }
  return merged;
}

function structuredText(message: SourcecadoStructuredMessage): string {
  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("");
}

export function restoreConversation(
  conversation: Conversation,
): SourcecadoStructuredMessage[] {
  const legacy = structureLegacyTranscript(
    conversation.id,
    conversation.messages,
  );
  const events = conversation.events ?? [];
  if (events.length === 0) return legacy;

  const eventStore = new SourcecadoChatStore(
    [{ id: conversation.id, messages: [] }],
    conversation.id,
  );
  eventStore.replayChatEvents(events);
  const restored = [...legacy];
  let cursor = 0;
  for (const message of eventStore.messagesFor(conversation.id)) {
    const match = restored.findIndex(
      (candidate, index) =>
        index >= cursor &&
        candidate.role === "assistant" &&
        structuredText(candidate) === structuredText(message),
    );
    if (match < 0) {
      restored.push(message);
      continue;
    }
    restored[match] = message;
    cursor = match + 1;
  }
  return restored;
}
