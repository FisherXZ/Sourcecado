import {
  MessagePrimitive,
  type TextMessagePartProps,
} from "@assistant-ui/react";

import { MessageFileChips } from "./AttachmentChips";
import { ContextChips } from "./ContextChips";
import { EditMessageAction } from "./MessageActions";
import { splitContext } from "./messageContext";

function UserText({ text }: TextMessagePartProps) {
  // Context the director attached travels as lines after the text. Show it as
  // chips, as it looked when it was attached.
  const { body, chips, files } = splitContext(text);
  return (
    <>
      <p>{body}</p>
      <ContextChips chips={chips} />
      <MessageFileChips ids={files} />
    </>
  );
}

export function UserMessage() {
  return (
    <MessagePrimitive.Root className="sourcecado-user-message">
      <MessagePrimitive.Parts components={{ Text: UserText }} />
      <EditMessageAction />
    </MessagePrimitive.Root>
  );
}
