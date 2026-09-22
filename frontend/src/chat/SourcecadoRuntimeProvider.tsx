import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type AddToolResultOptions,
  type AppendMessage,
  type RespondToToolApprovalOptions,
  type ExternalThreadQueueAdapter,
} from "@assistant-ui/react";
import type { ReactNode } from "react";

import {
  convertStructuredMessage,
  type SourcecadoStructuredMessage,
} from "./messageAdapter";

type SourcecadoRuntimeProviderProps = {
  readonly children: ReactNode;
  readonly messages: readonly SourcecadoStructuredMessage[];
  readonly running: boolean;
  readonly onNew: (message: AppendMessage) => Promise<void> | void;
  readonly onCancel?: () => Promise<void> | void;
  readonly onAddToolResult?: (options: AddToolResultOptions) => Promise<void> | void;
  readonly onRespondToToolApproval?: (
    options: RespondToToolApprovalOptions,
  ) => Promise<void> | void;
  readonly queue?: ExternalThreadQueueAdapter;
};

export function SourcecadoRuntimeProvider({
  children,
  messages,
  running,
  onNew,
  onCancel,
  onAddToolResult,
  onRespondToToolApproval,
  queue,
}: SourcecadoRuntimeProviderProps) {
  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: convertStructuredMessage,
    isRunning: running,
    isSendDisabled: running && !queue,
    onNew: async (message) => onNew(message),
    onCancel: onCancel ? async () => onCancel() : undefined,
    onAddToolResult,
    onRespondToToolApproval,
    queue,
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}
