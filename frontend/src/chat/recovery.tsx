import { createContext, useContext, type ReactNode } from "react";

import type { CommandDelivery } from "../api";
import type { ToolFailure } from "./protocol";
import type { StepState } from "./processState";

export type RecoveryAction = "retry" | "repair" | "continue";

type RecoveryContextValue = {
  /** Sends the command. The delivery says whether it left the browser. */
  readonly act: (
    action: RecoveryAction,
    failure: ToolFailure,
  ) => CommandDelivery | void;
};

const RecoveryContext = createContext<RecoveryContextValue>({
  act: () => {},
});

export function RecoveryProvider({
  children,
  onAction,
}: {
  readonly children: ReactNode;
  readonly onAction: RecoveryContextValue["act"];
}) {
  return (
    <RecoveryContext.Provider value={{ act: onAction }}>
      {children}
    </RecoveryContext.Provider>
  );
}

export function useRecoveryActions(): RecoveryContextValue {
  return useContext(RecoveryContext);
}

/**
 * What the operator may do about one failed step.
 *
 * The backend owns the policy. This reads the facts it already sends with a
 * failure (`retry_safe`, `repair_route`, the failure class and the last
 * recovery status) and offers an action only where the backend can carry it
 * out. It adds no permission of its own.
 */
export type RecoveryChoices = {
  /**
   * Run the step again. Offered only for a step the backend calls
   * retry-safe: a tool that reads. A send, a paid call, a write or a command
   * is never offered here. The operator asks for it again in a new message,
   * and the new request asks for its own approval.
   */
  readonly retry: boolean;
  /** Where the connection can be repaired, when the backend names a place. */
  readonly repairRoute: string | null;
  /** Skip this source and keep the work that already finished. */
  readonly continueWithout: boolean;
};

/** Recovery statuses after which the backend has nothing left to offer. */
const SETTLED_STATUSES = new Set(["succeeded", "skipped", "denied", "interrupted"]);

export function recoveryChoices(
  state: StepState,
  failure: ToolFailure | null,
  recovery: Record<string, unknown> | null,
  /** The turn ended in a provider failure. Its notice carries the one Retry. */
  turnFailed = false,
): RecoveryChoices | null {
  if ((state !== "failed" && state !== "blocked") || failure === null) return null;
  const status = recovery?.status ? String(recovery.status) : null;
  if (status !== null && SETTLED_STATUSES.has(status)) return null;
  return {
    // A refusal by policy ("Not allowed") is not a fault to retry. A rejected
    // request would fail the same way again, and its summary already says the
    // request details need correcting first.
    retry:
      state === "failed" &&
      failure.retry_safe === true &&
      failure.class !== "validation" &&
      !turnFailed,
    repairRoute: failure.repair_route,
    continueWithout: true,
  };
}

/** Two to four words that say why a step failed. The full summary stays in the detail. */
export function failureReason(failure: ToolFailure): string {
  switch (failure.class) {
    case "connector_auth":
      return "Needs repair";
    case "timeout_network":
      return "Could not connect";
    case "permission":
      return "Not allowed";
    case "validation":
      return "Request rejected";
    default:
      return "Failed";
  }
}
