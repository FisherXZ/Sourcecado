export function WelcomePage({ onStartChat }: { onStartChat: () => void }) {
  return (
    <main className="route-page welcome-page">
      <p className="eyebrow">Your sourcing workspace</p>
      <h1>Welcome to Sourcecado</h1>
      <p>Create a conversation or connect a source to begin your operator workflow.</p>
      <div className="welcome-actions">
        <button type="button" onClick={onStartChat}>Start a chat</button>
        <a href="#/connections">Open Connections</a>
      </div>
    </main>
  );
}
