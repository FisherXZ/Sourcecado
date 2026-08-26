import type { Conversation } from "../api";
import {
  structureLegacyTranscript,
  type SourcecadoStructuredMessage,
} from "./messageAdapter";
import { SourcecadoChatStore, tagRestoredThread } from "./store";

function structuredText(message: SourcecadoStructuredMessage): string {
  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("");
}

/**
 * Maps structured legacy ids (`<thread>:legacy:<index>`) to the sidecar
 * message identity persisted on the raw record, when one exists.
 */
function legacyIdentities(conversation: Conversation): Map<string, string> {
  const identities = new Map<string, string>();
  conversation.messages.forEach((record, index) => {
    if (typeof record.message_id === "string" && record.message_id.length > 0) {
      identities.set(`${conversation.id}:legacy:${index}`, record.message_id);
    }
  });
  return identities;
}

function spliceByIdentity(
  restored: SourcecadoStructuredMessage[],
  cursor: number,
  identities: ReadonlyMap<string, string>,
  message: SourcecadoStructuredMessage,
): number | null {
  const start = restored.findIndex(
    (candidate, index) =>
      index >= cursor && identities.get(candidate.id) === message.id,
  );
  if (start < 0) return null;
  let end = start;
  while (
    end + 1 < restored.length &&
    identities.get(restored[end + 1]!.id) === message.id
  ) {
    end += 1;
  }
  restored.splice(start, end - start + 1, message);
  return start + 1;
}

/**
 * Fallback for legacy records with no persisted identity: replace the
 * contiguous assistant block whose concatenated text matches the projection.
 * A run that used tools persists one legacy record per step, while its event
 * projection is a single message, so the whole block must collapse into it.
 */
function spliceByText(
  restored: SourcecadoStructuredMessage[],
  cursor: number,
  message: SourcecadoStructuredMessage,
): number | null {
  const target = structuredText(message);
  if (target.trim().length === 0) return null;
  for (let start = cursor; start < restored.length; start += 1) {
    if (restored[start]!.role !== "assistant") continue;
    let joined = "";
    for (let end = start; end < restored.length; end += 1) {
      if (restored[end]!.role !== "assistant") break;
      joined += structuredText(restored[end]!);
      if (joined === target || joined.trim() === target.trim()) {
        restored.splice(start, end - start + 1, message);
        return start + 1;
      }
      if (!target.startsWith(joined)) break;
    }
  }
  return null;
}

export function restoreConversation(
  conversation: Conversation,
): SourcecadoStructuredMessage[] {
  const legacy = structureLegacyTranscript(
    conversation.id,
    conversation.messages,
  );
  const events = conversation.events ?? [];
  const snapshotEventIds = new Set<string>();
  for (const event of events) {
    if ("version" in event) snapshotEventIds.add(event.event_id);
  }
  if (events.length === 0) return tagRestoredThread(legacy, snapshotEventIds);

  const identities = legacyIdentities(conversation);
  const eventStore = new SourcecadoChatStore(
    [{ id: conversation.id, messages: [] }],
    conversation.id,
  );
  eventStore.replayChatEvents(events);
  const restored = [...legacy];
  let cursor = 0;
  for (const message of eventStore.messagesFor(conversation.id)) {
    const next =
      spliceByIdentity(restored, cursor, identities, message) ??
      spliceByText(restored, cursor, message);
    if (next === null) {
      restored.push(message);
      continue;
    }
    cursor = next;
  }
  return tagRestoredThread(restored, snapshotEventIds);
}
