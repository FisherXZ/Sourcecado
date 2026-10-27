import { createContext, useContext } from "react";

/**
 * True for everything drawn inside the inspector's Output view. A result card
 * there is a second copy of the card in the thread. It must not repeat the
 * thread card's anchor, and it must not offer to open the item that is
 * already open.
 */
const InInspectorContext = createContext(false);

export const InInspector = InInspectorContext.Provider;

export function useInInspector(): boolean {
  return useContext(InInspectorContext);
}
