import {
  MessagePrimitive,
  type TextMessagePartProps,
} from "@assistant-ui/react";

import { ContextChips } from "./ContextChips";
import { splitContext } from "./messageContext";

function UserText({ text }: TextMessagePartProps) {
  // Context the director attached travels as lines after the text. Show it as
  // chips, as it looked when it was attached.
  const { body, chips } = splitContext(text);
  return (
    <>
      <p>{body}</p>
      <ContextChips chips={chips} />
    </>
  );
}

export function UserMessage() {
  return (
    <MessagePrimitive.Root className="sourcecado-user-message">
      <MessagePrimitive.Parts components={{ Text: UserText }} />
    </MessagePrimitive.Root>
  );
}
