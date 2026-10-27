import {
  useAui,
  useAuiState,
  type ThreadMessage,
  type ToolCallMessagePart,
} from "@assistant-ui/react";

import { ActionIcon, StatusIcon } from "./ProcessIcons";
import { turnRetryBlock } from "./retrySafety";

const PROVIDER_NAMES: Readonly<Record<string, string>> = {
  deepseek: "DeepSeek",
  kimi: "Kimi",
  moonshot: "Moonshot",
  openai: "OpenAI",
  anthropic: "Anthropic",
  openai_compatible: "OpenAI-compatible provider",
};

/** "OpenAI · gpt-4o-mini", or null when the backend did not name a provider. */
export function providerLine(
  provider: unknown,
  model: unknown,
): string | null {
  if (typeof provider !== "string" || provider === "" || provider === "unknown") {
    return null;
  }
  const name =
    PROVIDER_NAMES[provider] ??
    `${provider.charAt(0).toUpperCase()}${provider.slice(1).replaceAll("_", " ")}`;
  return typeof model === "string" && model !== "" && model !== "unknown"
    ? `${name} · ${model}`
    : name;
}

function textOf(content: ThreadMessage["content"]): string {
  return content
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("\n");
}

/**
 * The first lines of a failed-turn notice: the x icon, the title, the provider
 * that failed, and a Retry icon button on the right.
 *
 * Retry belongs to the newest failure only, because it starts a new turn at
 * the end of the thread. It is a different action from a failed step's Retry,
 * which runs one step again in place. The message action bar under a finished
 * answer never shows on a failed turn, so the two turn-level Retry buttons
 * never share a turn.
 */
export function TurnNoticeHead({
  title,
  provider,
  model,
}: {
  readonly title: string;
  readonly provider?: unknown;
  readonly model?: unknown;
}) {
  const aui = useAui();
  const isLast = useAuiState((state) => state.message.isLast);
  const running = useAuiState((state) => state.thread.isRunning);
  // The operator message the failed turn answered: the nearest one before this notice.
  const prompt = useAuiState((state) => {
    for (let at = state.message.index - 1; at >= 0; at -= 1) {
      const earlier = state.thread.messages[at];
      if (earlier?.role === "user") return textOf(earlier.content);
    }
    return "";
  });
  // The failed turn is the assistant message between that operator message and this notice.
  const block = useAuiState((state) => {
    for (let at = state.message.index - 1; at >= 0; at -= 1) {
      const earlier = state.thread.messages[at];
      if (earlier?.role === "user") return null;
      if (
        earlier?.role === "assistant" &&
        earlier.metadata.custom?.sourcecadoNotice !== true
      ) {
        return turnRetryBlock(
          earlier.content.filter(
            (part): part is ToolCallMessagePart => part.type === "tool-call",
          ),
          earlier.metadata.custom?.sourcecadoState,
        );
      }
    }
    return null;
  });
  const reason = running
    ? "A run is in progress."
    : prompt.trim() === ""
      ? "There is no earlier message to send again."
      : block;
  const failedProvider = providerLine(provider, model);

  return (
    <>
      <div className="sourcecado-notice-head">
        <StatusIcon state="failed" />
        <p className="sourcecado-notice-title">{title}</p>
        {isLast ? (
          <button
            type="button"
            className="sourcecado-step-icon-button"
            aria-label={reason ? `Retry unavailable. ${reason}` : "Retry this turn"}
            aria-disabled={reason ? true : undefined}
            title={reason ?? "Retry this turn"}
            onClick={
              reason
                ? undefined
                : // thread().append starts the turn the way the composer does,
                  // without touching a draft the operator is typing.
                  () => aui.thread().append(prompt)
            }
          >
            <ActionIcon name="retry" />
          </button>
        ) : null}
      </div>
      {failedProvider ? (
        <p className="sourcecado-notice-provider">
          <span>Provider</span> {failedProvider}
        </p>
      ) : null}
    </>
  );
}
