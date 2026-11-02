import { useEffect, type ReactNode } from "react";

/** The icon, title and one-line detail that every row of the composer menus shares. */
export function MenuRow({
  icon,
  title,
  detail,
}: {
  readonly icon: ReactNode;
  readonly title: string;
  readonly detail?: string;
}) {
  return (
    <>
      <span className="sourcecado-menu-icon">{icon}</span>
      <span className="sourcecado-menu-text">
        <span className="sourcecado-menu-title">{title}</span>
        {detail ? (
          <span className="sourcecado-menu-detail" title={detail}>
            {detail}
          </span>
        ) : null}
      </span>
    </>
  );
}

/** Keep the row the keyboard chose in view, so Enter never picks a row the director cannot see. */
export function useScrollActiveIntoView(activeId: string | undefined): void {
  useEffect(() => {
    if (activeId) document.getElementById(activeId)?.scrollIntoView?.({ block: "nearest" });
  }, [activeId]);
}
