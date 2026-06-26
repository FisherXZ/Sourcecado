import type { NavItem } from "@/components/ui";

// Live routes only. Downstream slices append their entry here as pages land
// (e.g. Contacts, Run Ledger, Drafts, Memory, Routines).
export const NAV: NavItem[] = [
  { label: "Research Chat", href: "/chat" },
  { label: "Memory", href: "/memory" },
];
