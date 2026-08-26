import { createContext, useContext, type ReactNode } from "react";

type PopulateComposer = (draft: string) => void;

const ComposerDraftContext = createContext<PopulateComposer | null>(null);

export function ComposerDraftProvider({
  populate,
  children,
}: {
  readonly populate: PopulateComposer;
  readonly children: ReactNode;
}) {
  return (
    <ComposerDraftContext.Provider value={populate}>
      {children}
    </ComposerDraftContext.Provider>
  );
}

export function usePopulateComposer(): PopulateComposer | null {
  return useContext(ComposerDraftContext);
}
