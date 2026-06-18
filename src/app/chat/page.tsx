import { EmptyState } from "@/components/ui";

export default function ChatPage() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center px-6">
      <EmptyState
        title="Research Chat is coming soon"
        description="Ask sourcing questions here and get cited answers with knowledge gaps. The agent run lands in Feature A."
      />
    </div>
  );
}
