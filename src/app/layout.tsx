import type { Metadata } from "next";
import localFont from "next/font/local";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import { AppShell } from "@/components/ui";
import { NAV } from "@/lib/nav";
import { currentUser } from "@/lib/auth/actor";
import { signOut } from "@/auth";

const generalSans = localFont({
  src: [
    { path: "./fonts/GeneralSans-400.woff2", weight: "400", style: "normal" },
    { path: "./fonts/GeneralSans-500.woff2", weight: "500", style: "normal" },
    { path: "./fonts/GeneralSans-600.woff2", weight: "600", style: "normal" },
    { path: "./fonts/GeneralSans-700.woff2", weight: "700", style: "normal" },
  ],
  variable: "--font-general-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Sourcecado",
  description: "Hosted team sourcing operating system for Codeology",
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  // Signed out (i.e. /login) renders bare: the nav and user chip would
  // otherwise advertise routes the visitor cannot reach.
  const user = await currentUser();

  return (
    <html
      lang="en"
      data-theme="light"
      className={`${generalSans.variable} ${GeistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full bg-canvas text-text font-sans">
        {user ? (
          <AppShell
            nav={NAV}
            user={{ name: user.name, role: user.email }}
            userAction={
              <form
                action={async () => {
                  "use server";
                  await signOut({ redirectTo: "/login" });
                }}
              >
                <button
                  type="submit"
                  className="text-[11px] text-muted underline hover:text-accent-deep"
                >
                  Sign out
                </button>
              </form>
            }
          >
            {children}
          </AppShell>
        ) : (
          children
        )}
      </body>
    </html>
  );
}
