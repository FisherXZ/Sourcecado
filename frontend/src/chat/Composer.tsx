import { ComposerPrimitive, useAui, useAuiState } from "@assistant-ui/react";
import { useEffect, useRef } from "react";

export function Composer({
  initialDraft,
  onDraftChange,
}: {
  readonly initialDraft: string;
  readonly onDraftChange: (draft: string) => void;
}) {
  const aui = useAui();
  const running = useAuiState((state) => state.thread.isRunning);
  const hydrated = useRef(false);
  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    aui.composer.setText(initialDraft);
  }, [aui, initialDraft]);

  return (
    <>
      <ComposerPrimitive.Root className="sourcecado-composer">
        <ComposerPrimitive.Input
          aria-label="Message Sourcecado"
          placeholder="Ask Sourcecado about this week’s sourcing work"
          rows={2}
          onChange={(event) => onDraftChange(event.currentTarget.value)}
        />
        {running ? (
          <ComposerPrimitive.Cancel
            className="sourcecado-composer-action"
            aria-label="Stop run"
          >
            <span className="sourcecado-composer-action-visual" aria-hidden="true">
              <span className="sourcecado-stop-icon" />
            </span>
          </ComposerPrimitive.Cancel>
        ) : (
          <ComposerPrimitive.Send
            className="sourcecado-composer-action"
            aria-label="Send message"
          >
            <span className="sourcecado-composer-action-visual" aria-hidden="true">
              <svg viewBox="0 0 20 20" focusable="false">
                <path d="M10 15.5v-11M5.5 9 10 4.5 14.5 9" />
              </svg>
            </span>
          </ComposerPrimitive.Send>
        )}
      </ComposerPrimitive.Root>
      <p
        className={`sourcecado-composer-running ${running ? "is-visible" : ""}`}
        aria-hidden={!running}
      >
        You can keep drafting while Sourcecado works.
      </p>
    </>
  );
}
