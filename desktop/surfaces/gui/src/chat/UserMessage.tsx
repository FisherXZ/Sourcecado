import {
  MessagePrimitive,
  type TextMessagePartProps,
} from "@assistant-ui/react";

function UserText({ text }: TextMessagePartProps) {
  return <p>{text}</p>;
}

export function UserMessage() {
  return (
    <MessagePrimitive.Root className="sourcecado-user-message">
      <MessagePrimitive.Parts components={{ Text: UserText }} />
    </MessagePrimitive.Root>
  );
}
