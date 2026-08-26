import type { ButtonHTMLAttributes, ReactNode } from "react";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost";
  children: ReactNode;
};

const base =
  "inline-flex items-center gap-2 rounded-[6px] px-3 py-1.5 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-accent-tint disabled:opacity-50 disabled:pointer-events-none";
const variants = {
  primary: "bg-accent text-white hover:bg-accent-deep",
  ghost: "bg-transparent border border-border text-text hover:border-accent hover:text-accent-deep",
};

// Exposed so non-<button> elements (e.g. a Next.js <Link> used as a CTA) can wear
// the button look without nesting an interactive element inside another.
export function buttonClasses(variant: "primary" | "ghost" = "primary", className = "") {
  return `${base} ${variants[variant]} ${className}`;
}

export function Button({ variant = "primary", children, className = "", ...rest }: Props) {
  return (
    <button type={rest.type ?? "button"} className={buttonClasses(variant, className)} {...rest}>
      {children}
    </button>
  );
}
