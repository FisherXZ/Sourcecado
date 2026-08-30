import { ThreadPrimitive, useAui } from "@assistant-ui/react";

import { AssistantMessage } from "./AssistantMessage";
import { Composer } from "./Composer";
import { ComposerDraftProvider } from "./ComposerDraftContext";
import { Queue } from "./Queue";
import { ThreadHeader } from "./ThreadHeader";
import { UserMessage } from "./UserMessage";
import type { ActivePerson, CurrentRunMetrics, QueueItem } from "../api";
import { Inspector } from "./Inspector";

type ThreadViewProps = {
  readonly title: string | null;
  readonly activePerson?: ActivePerson | null;
  readonly personaName?: string | null;
  readonly runMetrics?: CurrentRunMetrics | null;
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

const SUGGESTED_ACTION = "Review the active contacts that need follow-up this week";

function SuggestedAction({
  label,
  prompt,
  onDraftChange,
}: {
  readonly label: string;
  readonly prompt: string;
  readonly onDraftChange: (draft: string) => void;
}) {
  const aui = useAui();
  return (
    <button
      type="button"
      className="sourcecado-suggestion"
      onClick={() => {
        aui.composer.setText(prompt);
        onDraftChange(prompt);
      }}
    >
      <span className="sourcecado-suggestion-icon" aria-hidden="true">
        <svg viewBox="0 0 20 20" focusable="false">
          <path d="M4 14.5c1.8-3.5 4.2-5.7 7.4-6.8M9.8 4.5l3.8 2.7-2.7 3.8" />
        </svg>
      </span>
      <span>{label}</span>
    </button>
  );
}

export function ThreadView({
  title,
  activePerson,
  personaName,
  runMetrics,
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
  const populateComposer = (draft: string) => {
    aui.composer.setText(draft);
    onDraftChange(draft);
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
    <ThreadPrimitive.Root className="sourcecado-thread">
      <ThreadHeader
        title={title}
        activePerson={activePerson}
        personaName={personaName}
        runMetrics={runMetrics}
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
            <ThreadPrimitive.Empty>
              <section className="sourcecado-empty-thread">
                <h2>What are you sourcing today?</h2>
                <p>Name a target, review an active sequence, or prepare outreach.</p>
              </section>
            </ThreadPrimitive.Empty>
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
        {!loading && !loadError ? (
          <ThreadPrimitive.Empty>
            <SuggestedAction
              label={SUGGESTED_ACTION}
              prompt={`${SUGGESTED_ACTION}.`}
              onDraftChange={onDraftChange}
            />
          </ThreadPrimitive.Empty>
        ) : null}
        <Composer initialDraft={initialDraft} onDraftChange={onDraftChange} />
      </div>
    </ThreadPrimitive.Root>
    <Inspector />
    </div>
    </ComposerDraftProvider>
  );
}
