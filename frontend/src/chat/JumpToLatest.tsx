import { ThreadPrimitive, useThreadViewport } from "@assistant-ui/react";

import { ArrowDownIcon } from "./icons";

/**
 * The way back to the newest output after the operator scrolled up to read.
 *
 * The thread library already follows new output only while the viewport is at
 * the bottom, and stops following when the operator scrolls up. It also keeps
 * `isAtBottom` current. This button reads that flag, so it shows exactly when
 * newer content is below. The library's own scroll-to-bottom turns following
 * back on.
 *
 * The jump is instant. There is no scroll animation, so nothing here depends
 * on the reduced-motion setting. Only the button's fade-in does, in CSS.
 */
export function JumpToLatest() {
  const atBottom = useThreadViewport((state) => state.isAtBottom);
  if (atBottom) return null;
  return (
    // Zero height, so it takes no space in the thread grid. The button floats
    // up from the top edge of the composer area.
    <div className="sourcecado-jump-anchor">
      <ThreadPrimitive.ScrollToBottom
        className="sourcecado-jump-latest"
        aria-label="Jump to latest"
        title="Jump to latest"
        onClick={(event) => {
          // The button leaves the page once the thread is at the bottom.
          // Hand focus to the transcript so keyboard reading carries on.
          event.currentTarget
            .closest(".sourcecado-thread")
            ?.querySelector<HTMLElement>('[role="log"]')
            ?.focus({ preventScroll: true });
        }}
      >
        <ArrowDownIcon />
      </ThreadPrimitive.ScrollToBottom>
    </div>
  );
}
