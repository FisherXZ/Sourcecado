import { redirect } from "next/navigation";
import { auth, signIn } from "@/auth";
import { AvocadoMark, Button, Card } from "@/components/ui";

// Onboarding surface — one of the few places DESIGN.md puts avocado character
// front and center (the dense tables stay quiet).
export default async function LoginPage() {
  const session = await auth();
  if (session?.user?.id) redirect("/chat");

  return (
    <main className="flex min-h-screen items-center justify-center bg-canvas px-6">
      <Card className="w-full max-w-[380px] text-center">
        <div className="px-4 py-5">
        <div className="mb-5 flex justify-center">
          <AvocadoMark />
        </div>
        <h1 className="text-[20px] font-semibold text-text">Sourcecado</h1>
        <p className="mt-2 text-[13px] text-muted">
          Sign in to pick up where your sourcing left off.
        </p>
        <form
          action={async () => {
            "use server";
            await signIn("google", { redirectTo: "/chat" });
          }}
          className="mt-6"
        >
          <Button type="submit" className="w-full justify-center">
            Continue with Google
          </Button>
        </form>
        <p className="mt-4 text-[11px] text-muted">Codeology Google accounts.</p>
        </div>
      </Card>
    </main>
  );
}
