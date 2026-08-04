import { redirect } from "next/navigation";
import { getDb } from "@/lib/db";
import { createSession, getOrCreateLatestSession, loadSessionMessages } from "@/lib/chat/sessions";
import { requireActor } from "@/lib/auth/actor";
import { mapMessagesToResumedExchanges } from "./resume";
import { ChatClient } from "./ChatClient";

export default async function ChatPage({
  searchParams,
}: {
  searchParams: Promise<{ new?: string }>;
}) {
  const { new: forceNew } = await searchParams;
  const db = getDb();
  const actor = await requireActor();

  // Presence of `?new` starts a fresh session, but guard the obvious falsy
  // strings — `?new=0` / `?new=false` read as "not new" and shouldn't create
  // one (the "New chat" link always passes `?new=1`).
  if (forceNew && forceNew !== "0" && forceNew !== "false") {
    await createSession(db, actor);
    redirect("/chat");
  }

  const session = await getOrCreateLatestSession(db, actor);
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
