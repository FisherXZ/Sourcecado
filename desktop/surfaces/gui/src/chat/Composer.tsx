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
    <ComposerPrimitive.Root className="sourcecado-composer">
      <ComposerPrimitive.Input
        aria-label="Message Sourcecado"
        placeholder="Ask Sourcecado about this week’s sourcing work"
        rows={2}
        onChange={(event) => onDraftChange(event.currentTarget.value)}
      />
      <ComposerPrimitive.Send aria-label="Send message">
        Send
      </ComposerPrimitive.Send>
      {running ? (
        <ComposerPrimitive.Cancel aria-label="Stop run">
          Stop
        </ComposerPrimitive.Cancel>
      ) : null}
      {running ? (
        <p className="sourcecado-composer-running">
          You can keep drafting while Sourcecado works.
        </p>
      ) : null}
    </ComposerPrimitive.Root>
  );
}
