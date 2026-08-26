import {
  createMessageQueue,
  type AppendMessage,
  type ExternalThreadQueueAdapter,
  type MessageQueueController,
} from "@assistant-ui/react";

type SourcecadoQueuePolicyOptions = {
  readonly dispatch: (message: AppendMessage) => void;
  readonly cancelTransport: () => void;
};

export class SourcecadoQueuePolicy {
  private readonly controller: MessageQueueController;
  private readonly cancelTransport: () => void;
  readonly adapter: ExternalThreadQueueAdapter;

  constructor(options: SourcecadoQueuePolicyOptions) {
    this.cancelTransport = options.cancelTransport;
    this.controller = createMessageQueue({
      run: (message) => options.dispatch(message),
    });
    this.adapter = this.controller.adapter;
  }

  beginRun(): void {
    this.controller.notifyBusy();
  }

  enqueue(message: AppendMessage): void {
    this.adapter.enqueue(message);
  }

  cancelRun(): void {
    this.controller.notifyCancelled();
    this.cancelTransport();
  }

  settleRun(): void {
    this.controller.notifyIdle();
  }

  resumeQueuedRun(): void {
    this.controller.notifyBusy();
    this.controller.notifyIdle();
  }

  pendingPrompts(): readonly string[] {
    return this.adapter.items.map((item) => item.prompt);
  }
}
