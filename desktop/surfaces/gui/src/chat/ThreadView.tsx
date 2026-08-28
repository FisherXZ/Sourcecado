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

function Starter({
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
      onClick={() => {
        aui.composer.setText(prompt);
        onDraftChange(prompt);
      }}
    >
      {label}
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
            <p>Your draft is still here. Retry when the sidecar is available.</p>
            <button type="button" onClick={onRetry}>
              Retry loading conversation
            </button>
          </div>
        ) : (
          <>
            <ThreadPrimitive.Empty>
              <section className="sourcecado-empty-thread">
                <img
                  className="sourcecado-empty-mascot"
                  src="/brand/mascot/owl-mapping.jpg"
                  alt=""
                  width={96}
                  height={96}
                />
                <p className="eyebrow">Start with a sourcing outcome</p>
                <p>Build a shortlist, find a why-now signal, or prepare outreach for review.</p>
                <div className="sourcecado-starters">
                  <Starter
                    label="Build a candidate shortlist"
                    prompt="Build a candidate shortlist for this week’s highest-priority role."
                    onDraftChange={onDraftChange}
                  />
                  <Starter
                    label="Find why-now signals"
                    prompt="Find fresh why-now signals for the people we should work next."
                    onDraftChange={onDraftChange}
                  />
                  <Starter
                    label="Prepare outreach for review"
                    prompt="Prepare personalized outreach drafts for review; do not send them."
                    onDraftChange={onDraftChange}
                  />
                </div>
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
      <Composer initialDraft={initialDraft} onDraftChange={onDraftChange} />
    </ThreadPrimitive.Root>
    <Inspector />
    </div>
    </ComposerDraftProvider>
  );
}
