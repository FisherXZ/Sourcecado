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

export function Button({ variant = "primary", children, className = "", ...rest }: Props) {
  return (
    <button className={`${base} ${variants[variant]} ${className}`} {...rest}>
      {children}
    </button>
  );
}
