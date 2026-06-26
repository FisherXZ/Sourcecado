import type { FormEvent, KeyboardEvent } from "react";
import { Button } from "@/components/ui";

// Sticky composer. Enter sends, Shift+Enter inserts a newline. Disabled (and the
// button reads "Running…") while a turn is streaming.
export function Composer({
  value,
  onChange,
  onSubmit,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  disabled: boolean;
}) {
  function submit() {
    if (!disabled && value.trim()) onSubmit();
  }
  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    submit();
  }
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex items-end gap-2 border-t border-border bg-surface px-4 py-3">
      <textarea
        aria-label="Message"
        rows={1}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask about a contact, company, or anything in memory…"
        className="max-h-40 min-h-[38px] flex-1 resize-none rounded-[6px] border border-border bg-canvas px-3 py-2 text-[13px] text-text outline-none placeholder:text-muted focus:border-accent focus:ring-[3px] focus:ring-accent-tint"
      />
      <Button type="submit" disabled={disabled || !value.trim()}>
        {disabled ? "Running…" : "Send"}
      </Button>
    </form>
  );
}
