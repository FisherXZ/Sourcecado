import type { ReactNode } from "react";

// User turns get the avocado-tint bubble (right); assistant turns render plain on
// the canvas (left) so the answer reads as content, not a nested card.
export function MessageBubble({ role, children }: { role: "user" | "assistant"; children: ReactNode }) {
  if (role === "user") {
    return (
      <div
        data-role="user"
        className="message-enter ml-auto max-w-[78%] rounded-[8px] bg-accent-tint px-3 py-2 text-[13px] text-text"
      >
        {children}
      </div>
    );
  }
  return (
    <div data-role="assistant" className="message-enter mr-auto max-w-[88%] text-[13px] leading-relaxed text-text">
      {children}
    </div>
  );
}
