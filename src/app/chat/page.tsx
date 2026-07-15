import { redirect } from "next/navigation";
import { getDb } from "@/lib/db";
import { createSession, getOrCreateLatestSession, loadSessionMessages } from "@/lib/chat/sessions";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import { mapMessagesToResumedExchanges } from "./resume";
import { ChatClient } from "./ChatClient";

export default async function ChatPage({
  searchParams,
}: {
  searchParams: Promise<{ new?: string }>;
}) {
  const { new: forceNew } = await searchParams;
  const db = getDb();

  if (forceNew) {
    await createSession(db, DEFAULT_ACTOR);
    redirect("/chat");
  }

  const session = await getOrCreateLatestSession(db, DEFAULT_ACTOR);
  const messages = await loadSessionMessages(db, session.id);
  const initialExchanges = mapMessagesToResumedExchanges(messages);

  return (
    <>
      <div className="mx-auto w-full max-w-3xl px-6 pt-4 text-right">
        <a href="/chat?new=1" className="text-[13px] text-accent-deep underline">
          New chat
        </a>
      </div>
      <ChatClient initialExchanges={initialExchanges} />
    </>
  );
}
