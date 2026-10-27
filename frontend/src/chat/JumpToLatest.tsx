import { ThreadPrimitive, useThreadViewport, useThreadViewportStore } from "@assistant-ui/react";
import { useEffect, type MutableRefObject } from "react";

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
          // The button leaves the page once the thread is at the bottom, so
          // focus needs a new home. A waiting approval comes first: the
          // operator reaches Allow and Deny in a few Tabs, not after the whole
          // transcript. Otherwise the transcript keeps keyboard reading going.
          const thread = event.currentTarget.closest(".sourcecado-thread");
          const target =
            thread?.querySelector<HTMLElement>(".sourcecado-approval-card h2") ??
            thread?.querySelector<HTMLElement>('[role="log"]');
          target?.focus({ preventScroll: true });
        }}
      >
        <ArrowDownIcon />
      </ThreadPrimitive.ScrollToBottom>
    </div>
  );
}

/**
 * Scrolls to the newest output when the operator sends a message of their own,
 * and only then.
 *
 * The thread library can also scroll to the bottom on every run start. That
 * includes a queued message that starts after the run before it ends, while
 * the operator is reading earlier output. The thread turns that option off, so
 * this component handles the send instead:
 * - a composer form submit inside the thread (typed Enter, the Send button,
 *   an edited message), when its box holds text;
 * - `scrollRef.current()`, for sends that are not a form submit (a card button).
 */
export function ScrollOnOwnSend({
  scrollRef,
}: {
  readonly scrollRef: MutableRefObject<() => void>;
}) {
  const viewport = useThreadViewportStore();
  useEffect(() => {
    const scroll = () => viewport.getState().scrollToBottom();
    scrollRef.current = scroll;
    const onSubmit = (event: Event) => {
      const form = event.target as HTMLElement;
      if (!form.closest(".sourcecado-thread")) return;
      if (form.querySelector("textarea")?.value.trim()) scroll();
    };
    // Capture phase: the composer clears its box while it handles the submit,
    // so this must read the text first.
    document.addEventListener("submit", onSubmit, true);
    return () => {
      document.removeEventListener("submit", onSubmit, true);
      scrollRef.current = () => {};
    };
  }, [viewport, scrollRef]);
  return null;
}
