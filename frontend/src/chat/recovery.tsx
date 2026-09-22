import { createContext, useContext, type ReactNode } from "react";

import type { ToolFailure } from "./protocol";

export type RecoveryAction = "retry" | "repair" | "continue";

type RecoveryContextValue = {
  readonly act: (action: RecoveryAction, failure: ToolFailure) => void;
};

const RecoveryContext = createContext<RecoveryContextValue>({
  act: () => {},
});

export function RecoveryProvider({
  children,
  onAction,
}: {
  readonly children: ReactNode;
  readonly onAction: (action: RecoveryAction, failure: ToolFailure) => void;
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
