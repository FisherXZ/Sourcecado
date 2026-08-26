import type { ReactNode } from "react";
import { AvocadoMark } from "./AvocadoMark";

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-[560px] rounded-[12px] border border-dashed border-border bg-raised p-11 text-center">
      <div className="flex justify-center">
        <AvocadoMark />
      </div>
      <h3 className="mb-1.5 mt-4 text-[18px] font-semibold">{title}</h3>
      <p className="mb-4 text-[14px] text-muted">{description}</p>
      {action && <div className="flex justify-center">{action}</div>}
    </div>
  );
}
