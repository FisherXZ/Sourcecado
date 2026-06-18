import type { Metadata } from "next";
import localFont from "next/font/local";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import { AppShell } from "@/components/ui";
import { NAV } from "@/lib/nav";

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

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      data-theme="light"
      className={`${generalSans.variable} ${GeistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full bg-canvas text-text font-sans">
        <AppShell nav={NAV} user={{ name: "Sourcing Director", role: "Codeology" }}>
          {children}
        </AppShell>
      </body>
    </html>
  );
}
