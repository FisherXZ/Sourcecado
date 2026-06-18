import Link from "next/link";
import { Button } from "@/components/ui";

export default function Home() {
  return (
    <div className="mx-auto flex min-h-[60vh] max-w-[640px] flex-col items-center justify-center gap-4 px-6 text-center">
      <h1 className="text-[32px] font-semibold tracking-tight">Sourcecado</h1>
      <p className="max-w-sm text-muted">
        Hosted sourcing operating system for Codeology. Ask questions about contacts,
        outreach history, and sourcing context — with cited answers.
      </p>
      <Link href="/chat">
        <Button>Open Research Chat</Button>
      </Link>
    </div>
  );
}
