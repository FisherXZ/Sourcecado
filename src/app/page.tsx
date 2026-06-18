import Link from "next/link";

export default function Home() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 text-center px-6">
      <h1 className="text-3xl font-semibold tracking-tight">SourcyAvo</h1>
      <p className="text-gray-500 max-w-sm">
        Sourcing memory system for Codeology. Ask questions about contacts,
        outreach history, and sourcing context.
      </p>
      <Link
        href="/chat"
        className="mt-2 px-5 py-2 rounded-full bg-gray-900 text-white text-sm hover:bg-gray-700 transition-colors"
      >
        Open Research Chat
      </Link>
    </div>
  );
}
