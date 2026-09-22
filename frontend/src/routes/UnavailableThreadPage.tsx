export function UnavailableThreadPage({ recentSessionId }: { recentSessionId: string | null }) {
  return (
    <main className="route-page unavailable-thread-page">
      <h1>Conversation unavailable</h1>
      <p>This conversation is unavailable.</p>
      {recentSessionId && (
        <a href={`#/chat/${encodeURIComponent(recentSessionId)}`}>Open most recent conversation</a>
      )}
    </main>
  );
}
