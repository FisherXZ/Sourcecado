import { ThreadPrimitive, useAui, useAuiState } from "@assistant-ui/react";
import { useRef } from "react";

import { AssistantMessage } from "./AssistantMessage";
import { Composer } from "./Composer";
import { ComposerDraftProvider, type ComposerRestore } from "./ComposerDraftContext";
import { EmptyGreeting, EmptyPrompts } from "./EmptyThread";
import { Queue } from "./Queue";
import { ThreadHeader, type RunStatus } from "./ThreadHeader";
import { UserMessage } from "./UserMessage";
import type { ActivePerson, QueueItem } from "../api";
import { Inspector } from "./Inspector";
import { JumpToLatest, ScrollOnOwnSend } from "./JumpToLatest";
import { UsageLine, type UsageSource } from "./UsageLine";

type ThreadViewProps = {
  readonly title: string | null;
  readonly activePerson?: ActivePerson | null;
  readonly runStatus: RunStatus | null;
  readonly elapsedMs: number | null;
  readonly usage: UsageSource | null;
  readonly loading: boolean;
  readonly loadError: boolean;
  readonly onRetry: () => void;
  readonly announcement: string;
  readonly initialDraft: string;
  readonly onDraftChange: (draft: string) => void;
  readonly queueItems: readonly QueueItem[];
  readonly queuePaused: boolean;
  readonly onQueueRetry: (itemId: string) => void;
  readonly onQueueResume: () => void;
  readonly onQueueMove: (itemId: string, beforeId?: string, afterId?: string) => void;
  readonly onQueueEdit: (itemId: string, text: string) => void;
  readonly onQueueRemove: (itemId: string) => void;
};

export function ThreadView({
  title,
  activePerson,
  runStatus,
  elapsedMs,
  usage,
  loading,
  loadError,
  onRetry,
  announcement,
  initialDraft,
  onDraftChange,
  queueItems,
  queuePaused,
  onQueueRetry,
  onQueueResume,
  onQueueMove,
  onQueueEdit,
  onQueueRemove,
}: ThreadViewProps) {
  const aui = useAui();
  // The empty thread is a greeting above the composer and prompt chips under
  // it, so it lives in the composer zone rather than in the transcript.
  const showEmptyState =
    useAuiState((state) => state.thread.isEmpty) && !loading && !loadError;
  // The composer owns its chips and files. It registers how to restore them,
  // so editing a message brings its files back as chips.
  const restoreRef = useRef<((restore: ComposerRestore) => void) | null>(null);
  const populateComposer = (draft: string, restore?: ComposerRestore) => {
    aui.composer.setText(draft);
    onDraftChange(draft);
    if (restore) restoreRef.current?.(restore);
  };
  // Built here on purpose: this is the thread's composer. A component
  // rendered under a message sees the edit composer instead, so it cannot
  // start a turn without this.
  const sendMessage = (text: string) => {
    aui.composer.setText(text);
    void aui.composer.send();
    onDraftChange("");
  };
  return (
    <ComposerDraftProvider populate={populateComposer} send={sendMessage}>
    <div className="sourcecado-chat-workspace">
    <ThreadPrimitive.ViewportProvider>
    <ThreadPrimitive.Root
      className={showEmptyState ? "sourcecado-thread is-empty" : "sourcecado-thread"}
    >
      <ThreadHeader
        title={title}
        activePerson={activePerson}
        status={runStatus}
        elapsedMs={elapsedMs}
      />
      <ThreadPrimitive.Viewport
        className="sourcecado-transcript"
        role="log"
        aria-label="Conversation"
        aria-busy={loading || undefined}
        // role="log" carries an IMPLICIT aria-live="polite"; omitting the
        // attribute does not disable that. Streaming is announced instead
        // through the dedicated .sourcecado-run-announcement status region
        // below, so the transcript itself must be explicitly non-live.
        aria-live="off"
        tabIndex={0}
        // A queued message that starts while the operator reads must not pull
        // the thread down. ScrollOnOwnSend scrolls for the operator's own send.
        scrollToBottomOnRunStart={false}
      >
        {loading ? (
          <div className="sourcecado-transcript-skeleton" aria-label="Loading conversation">
            <span />
            <span />
            <span />
          </div>
        ) : loadError ? (
          <div className="sourcecado-load-error" role="alert">
            <p>We couldn’t load this conversation.</p>
            <p>Your draft is still here. Retry, or choose another conversation.</p>
            <button type="button" onClick={onRetry}>
              Retry loading conversation
            </button>
          </div>
        ) : (
          <>
            <ThreadPrimitive.Messages
              components={{
                UserMessage,
                AssistantMessage,
              }}
            />
          </>
        )}
      </ThreadPrimitive.Viewport>
      <p
        className="sourcecado-run-announcement"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {announcement}
      </p>
      <ScrollOnOwnSend />
      <JumpToLatest />
      <Queue
        items={queueItems}
        paused={queuePaused}
        onRetry={onQueueRetry}
        onResume={onQueueResume}
        onMove={onQueueMove}
        onEdit={onQueueEdit}
        onRemove={onQueueRemove}
      />
      <div className="sourcecado-composer-zone">
        {showEmptyState ? <EmptyGreeting /> : null}
        {usage ? <UsageLine source={usage} /> : null}
        <Composer
          initialDraft={initialDraft}
          onDraftChange={onDraftChange}
          boundPerson={activePerson ?? null}
          restoreRef={restoreRef}
        />
        {showEmptyState ? <EmptyPrompts onPick={populateComposer} /> : null}
      </div>
    </ThreadPrimitive.Root>
    </ThreadPrimitive.ViewportProvider>
    <Inspector />
    </div>
    </ComposerDraftProvider>
  );
}
