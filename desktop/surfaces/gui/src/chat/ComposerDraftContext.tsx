import { createContext, useContext, type ReactNode } from "react";

type PopulateComposer = (draft: string) => void;

type ComposerActions = {
  /** Put text in the composer and leave sending to the director. */
  readonly populate: PopulateComposer;
  /** Send text as the director's own message on this thread. */
  readonly send: (text: string) => void;
};

const ComposerDraftContext = createContext<ComposerActions | null>(null);

export function ComposerDraftProvider({
  populate,
  send,
  children,
}: {
  readonly populate: PopulateComposer;
  readonly send: (text: string) => void;
  readonly children: ReactNode;
}) {
  return (
    <ComposerDraftContext.Provider value={{ populate, send }}>
      {children}
    </ComposerDraftContext.Provider>
  );
}

export function usePopulateComposer(): PopulateComposer | null {
  return useContext(ComposerDraftContext)?.populate ?? null;
}

/**
 * The thread composer's send, reachable from inside a message.
 *
 * A component rendered under a message has its own edit composer in scope, so
 * it cannot start a new turn on its own. This hands it the thread's composer,
 * which is the only way an in-message action can send the director's message.
 */
export function useSendComposer(): ((text: string) => void) | null {
  return useContext(ComposerDraftContext)?.send ?? null;
}
