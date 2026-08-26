import type {
  AppendMessage,
  RespondToToolApprovalOptions,
} from "@assistant-ui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getPersona,
  getSession,
  getSessions,
  hasToken,
  openChat,
  type ConnectionStatus,
  type SourcecadoSocketEvent,
  type QueueItem,
} from "../api";
import { restoreConversation } from "../chat/restoreConversation";
import { SourcecadoRuntimeProvider } from "../chat/SourcecadoRuntimeProvider";
import {
  createPersistedQueueAdapter,
  createQueueCommandId,
} from "../chat/persistedQueue";
import { SourcecadoChatStore } from "../chat/store";
import { ThreadView } from "../chat/ThreadView";
import {
  RecoveryProvider,
  type RecoveryAction,
} from "../chat/recovery";
import type { ToolFailure } from "../chat/protocol";
import { InspectorProvider } from "../chat/Inspector";

function appendText(message: AppendMessage): string {
  return message.content
    .filter((part) => part.type === "text")
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("")
    .trim();
}

function draftKey(threadId: string): string {
  return `sourcecado.chat.draft.v1:${encodeURIComponent(threadId)}`;
}

function readDraft(threadId: string): string {
  if (!threadId) return "";
  try {
    return window.localStorage.getItem(draftKey(threadId)) ?? "";
  } catch {
    return "";
  }
}

function writeDraft(threadId: string, draft: string): void {
  if (!threadId) return;
  try {
    if (draft) window.localStorage.setItem(draftKey(threadId), draft);
    else window.localStorage.removeItem(draftKey(threadId));
  } catch {
    // Draft persistence is best-effort; the live composer remains usable.
  }
}

export function ChatPage({ sessionId: requestedSessionId }: { sessionId?: string }) {
  const initialThreadId = requestedSessionId ?? "";
  const storeRef = useRef(
    new SourcecadoChatStore(
      initialThreadId ? [{ id: initialThreadId, messages: [] }] : [],
      initialThreadId,
    ),
  );
  const activeThreadRef = useRef(initialThreadId);
  const chatRef = useRef<ReturnType<typeof openChat> | null>(null);
  const loadVersionRef = useRef(0);
  const loadedThreadsRef = useRef(new Set<string>());
  const threadTitlesRef = useRef(new Map<string, string | null>());
  const threadQueuesRef = useRef(
    new Map<string, { items: readonly QueueItem[]; paused: boolean }>(),
  );
  const runningThreadsRef = useRef(new Set<string>());
  const activeRunsRef = useRef(new Map<string, string>());
  const nextLegacyIndexRef = useRef(new Map<string, number>());
  const connectionStatusRef = useRef<ConnectionStatus>("connected");
  const [activeThreadId, setActiveThreadId] = useState(initialThreadId);
  const [title, setTitle] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(initialThreadId));
  const [loadError, setLoadError] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [announcement, setAnnouncement] = useState("");
  const [personaName, setPersonaName] = useState<string | null>(null);
  const [, setRevision] = useState(0);

  const refresh = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    if (!hasToken()) return;
    let active = true;
    getPersona()
      .then((persona) => {
        if (active) setPersonaName(persona.name);
      })
      .catch(() => {
        // Persona is a header nicety; leave the header without it on failure.
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const loadVersion = ++loadVersionRef.current;
    let active = true;
    setLoadError(false);
    setTitle(null);

    async function loadThread() {
      if (!hasToken()) throw new Error("missing launch token");
      const threadId = requestedSessionId || (await getSessions()).open_id || "";
      if (!active || loadVersion !== loadVersionRef.current) return;
      activeThreadRef.current = threadId;
      setActiveThreadId(threadId);
      storeRef.current.selectThread(threadId);
      if (!threadId) {
        setLoading(false);
        refresh();
        return;
      }
      if (loadedThreadsRef.current.has(threadId)) {
        setTitle(threadTitlesRef.current.get(threadId) ?? null);
        setLoading(false);
        refresh();
        return;
      }
      setLoading(true);
      const conversation = await getSession(threadId);
      if (
        !active ||
        loadVersion !== loadVersionRef.current ||
        activeThreadRef.current !== threadId
      ) {
        return;
      }
      storeRef.current.replaceThread(threadId, restoreConversation(conversation));
      threadQueuesRef.current.set(threadId, {
        items: conversation.queue ?? [],
        paused: conversation.queue_paused ?? false,
      });
      loadedThreadsRef.current.add(threadId);
      threadTitlesRef.current.set(threadId, conversation.title);
      nextLegacyIndexRef.current.set(threadId, conversation.messages.length);
      setTitle(conversation.title);
      setLoading(false);
      setLoadError(false);
      refresh();
    }

    void loadThread().catch(() => {
      if (!active || loadVersion !== loadVersionRef.current) return;
      setLoading(false);
      setLoadError(true);
    });
    return () => {
      active = false;
    };
  }, [loadAttempt, refresh, requestedSessionId]);

  useEffect(() => {
    if (!hasToken()) return;
    const chat = openChat((event: SourcecadoSocketEvent) => {
      if (event.type === "queue_snapshot") {
        threadQueuesRef.current.set(event.session_id, {
          items: event.items,
          paused: event.paused,
        });
        if (event.session_id === activeThreadRef.current) refresh();
        return;
      }
      const applied = storeRef.current.applyChatEvent(event);
      if (applied.type === "connection_change") {
        connectionStatusRef.current = applied.status;
      } else if ("version" in applied) {
        const threadId = applied.session_id;
        if (applied.type === "turn_start") {
          runningThreadsRef.current.add(threadId);
          activeRunsRef.current.set(threadId, applied.run_id);
          if (threadId === activeThreadRef.current) {
            setAnnouncement("Run started.");
          }
        } else if (applied.type === "turn_stopping") {
          if (threadId === activeThreadRef.current) {
            setAnnouncement(applied.message);
          }
        } else if (applied.type === "permission_required") {
          if (threadId === activeThreadRef.current) {
            setAnnouncement("Sourcecado needs your approval to continue.");
          }
        } else if (applied.type === "turn_stopped") {
          runningThreadsRef.current.delete(threadId);
          activeRunsRef.current.delete(threadId);
          if (threadId === activeThreadRef.current) {
            setAnnouncement("Run cancelled.");
          }
        } else if (applied.type === "turn_end") {
          runningThreadsRef.current.delete(threadId);
          activeRunsRef.current.delete(threadId);
          if (threadId === activeThreadRef.current) {
            setAnnouncement(
              applied.state === "complete"
                ? "Run complete."
                : "Run cancelled.",
            );
          }
        } else if (applied.type === "error") {
          runningThreadsRef.current.delete(threadId);
          activeRunsRef.current.delete(threadId);
          if (threadId === activeThreadRef.current) {
            setAnnouncement("Run failed.");
          }
        }
      }
      refresh();
    });
    chatRef.current = chat;
    return () => {
      chat.close();
      if (chatRef.current === chat) chatRef.current = null;
    };
  }, [refresh]);

  async function handleNew(message: AppendMessage) {
    const text = appendText(message);
    const threadId = activeThreadRef.current;
    if (!text || !threadId || runningThreadsRef.current.has(threadId)) return;
    const legacyIndex = nextLegacyIndexRef.current.get(threadId) ?? 0;
    nextLegacyIndexRef.current.set(threadId, legacyIndex + 1);
    const messageId = `${threadId}:legacy:${legacyIndex}`;
    const partId = `${messageId}:text:0`;
    storeRef.current.apply({
      type: "message_started",
      threadId,
      messageId,
      role: "user",
    });
    storeRef.current.apply({
      type: "text_delta",
      threadId,
      messageId,
      partId,
      delta: text,
    });
    storeRef.current.apply({
      type: "message_finished",
      threadId,
      messageId,
      state: "complete",
    });
    if (connectionStatusRef.current === "connected") {
      // Only claim a run is active, and only drop the local draft backup,
      // once the message has somewhere to actually go right now. Otherwise
      // it is sitting in the transport's retry buffer, not "sent".
      runningThreadsRef.current.add(threadId);
      writeDraft(threadId, "");
      setAnnouncement("Run started.");
    } else {
      setAnnouncement(
        "Waiting for connection. Your message will send when Sourcecado reconnects.",
      );
    }
    refresh();
    chatRef.current?.send(text, threadId);
  }

  function handleApproval(options: RespondToToolApprovalOptions) {
    chatRef.current?.approve(
      options.approvalId,
      options.approved ? "allow" : "deny",
    );
  }

  function handleCancel() {
    const threadId = activeThreadRef.current;
    const runId = activeRunsRef.current.get(threadId);
    if (!threadId || !runId) return;
    chatRef.current?.cancel?.(threadId, runId);
  }

  function handleQueueRetry(itemId: string) {
    const sessionId = activeThreadRef.current;
    if (!sessionId) return;
    chatRef.current?.queue?.({
      type: "queue_retry",
      session_id: sessionId,
      command_id: createQueueCommandId(),
      item_id: itemId,
    });
  }

  function handleQueueResume() {
    const sessionId = activeThreadRef.current;
    if (!sessionId) return;
    chatRef.current?.queue?.({
      type: "queue_resume",
      session_id: sessionId,
      command_id: createQueueCommandId(),
    });
  }

  function handleQueueMove(
    itemId: string,
    beforeId?: string,
    afterId?: string,
  ) {
    const sessionId = activeThreadRef.current;
    if (!sessionId) return;
    chatRef.current?.queue?.({
      type: "queue_move",
      session_id: sessionId,
      command_id: createQueueCommandId(),
      item_id: itemId,
      ...(beforeId ? { before_id: beforeId } : {}),
      ...(afterId ? { after_id: afterId } : {}),
    });
  }

  function handleQueueEdit(itemId: string, text: string) {
    const sessionId = activeThreadRef.current;
    if (!sessionId) return;
    chatRef.current?.queue?.({
      type: "queue_edit",
      session_id: sessionId,
      command_id: createQueueCommandId(),
      item_id: itemId,
      text,
    });
  }

  function handleQueueRemove(itemId: string) {
    const sessionId = activeThreadRef.current;
    if (!sessionId) return;
    chatRef.current?.queue?.({
      type: "queue_remove",
      session_id: sessionId,
      command_id: createQueueCommandId(),
      item_id: itemId,
    });
  }

  function handleRecovery(action: RecoveryAction, failure: ToolFailure) {
    const type =
      action === "retry"
        ? "retry_failed_step"
        : action === "repair"
          ? "repair_connection"
          : "continue_without_source";
    chatRef.current?.recover?.({
      type,
      session_id: failure.session_id,
      run_id: failure.run_id,
      call_id: failure.call_id,
      command_id: createQueueCommandId(),
    });
  }

  const messages = storeRef.current.messagesFor(activeThreadId);
  const running = runningThreadsRef.current.has(activeThreadId);
  const queue = threadQueuesRef.current.get(activeThreadId) ?? {
    items: [],
    paused: false,
  };
  const queueAdapter = useMemo(
    () =>
      createPersistedQueueAdapter({
        sessionId: activeThreadId,
        items: queue.items,
        running,
        send: (command) => chatRef.current?.queue?.(command),
        dispatch: handleNew,
      }),
    [activeThreadId, queue.items, running],
  );

  return (
    <main className="chat-page">
      <RecoveryProvider onAction={handleRecovery}>
      <InspectorProvider threadId={activeThreadId}>
      <SourcecadoRuntimeProvider
        key={activeThreadId || "empty-thread"}
        messages={messages}
        running={running}
        onNew={handleNew}
        onCancel={handleCancel}
        onRespondToToolApproval={handleApproval}
        queue={queueAdapter}
      >
        <ThreadView
          title={title}
          personaName={personaName}
          loading={loading}
          loadError={loadError}
          onRetry={() => setLoadAttempt((attempt) => attempt + 1)}
          announcement={announcement}
          initialDraft={readDraft(activeThreadId)}
          onDraftChange={(draft) => writeDraft(activeThreadId, draft)}
          queueItems={queue.items}
          queuePaused={queue.paused}
          onQueueRetry={handleQueueRetry}
          onQueueResume={handleQueueResume}
          onQueueMove={handleQueueMove}
          onQueueEdit={handleQueueEdit}
          onQueueRemove={handleQueueRemove}
        />
      </SourcecadoRuntimeProvider>
      </InspectorProvider>
      </RecoveryProvider>
    </main>
  );
}
