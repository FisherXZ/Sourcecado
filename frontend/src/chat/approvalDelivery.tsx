import { createContext, useContext, type ReactNode } from "react";

import type { CommandDelivery } from "../api";

type ApprovalDeliveryRef = { readonly current: CommandDelivery | null };

const ApprovalDeliveryContext = createContext<ApprovalDeliveryRef>({
  current: null,
});

/**
 * assistant-ui's `part.respondToApproval` is a fire-and-forget bridge to the
 * runtime's `onRespondToToolApproval` adapter — it discards that handler's
 * return value, so a component below it has no way to learn whether the
 * underlying WebSocket command was actually delivered. `handleApproval`
 * (ChatPage) writes its `CommandDelivery` result into `deliveryRef`
 * synchronously before returning; since the whole call chain is synchronous,
 * a consumer can read `deliveryRef.current` right after calling
 * `part.respondToApproval` and see the real outcome.
 */
export function ApprovalDeliveryProvider({
  children,
  deliveryRef,
}: {
  readonly children: ReactNode;
  readonly deliveryRef: ApprovalDeliveryRef;
}) {
  return (
    <ApprovalDeliveryContext.Provider value={deliveryRef}>
      {children}
    </ApprovalDeliveryContext.Provider>
  );
}

export function useLastApprovalDelivery(): ApprovalDeliveryRef {
  return useContext(ApprovalDeliveryContext);
}
