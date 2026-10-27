import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { MenuRow } from "./MenuRow";
import { ActionIcon, StatusIcon } from "./ProcessIcons";
import type { ToolFailure } from "./protocol";
import { useRecoveryActions, type RecoveryChoices } from "./recovery";

/** Three dots, on the 16px grid of the process icons. */
function MoreIcon() {
  return (
    <svg
      className="sourcecado-process-icon"
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="3.5" cy="8" r="1.3" />
      <circle cx="8" cy="8" r="1.3" />
      <circle cx="12.5" cy="8" r="1.3" />
    </svg>
  );
}

/** A click that no recovery event answers within this time may be sent again. */
const SENT_RESET_MS = 15_000;
const MENU_WIDTH = 300;
const MENU_GAP = 6;
const VIEWPORT_MARGIN = 8;

type Placement = { readonly top: number; readonly left: number };

/**
 * The right edge of a failed step row: a Retry icon button and an overflow
 * menu with Retry, Repair connection and Continue without this source. Each
 * appears only where `recoveryChoices` says the backend can carry it out.
 *
 * The menu opens in a portal with fixed placement, because the step list is a
 * scroll container and would clip a menu that sat inside it.
 */
export function StepRecovery({
  label,
  failure,
  choices,
  recoveryKey,
}: {
  /** The step's own words, "Searching Drive". Gives each button a distinct name. */
  readonly label: string;
  readonly failure: ToolFailure;
  readonly choices: RecoveryChoices;
  /** Changes when the backend answers, which ends the "Retrying" wait. */
  readonly recoveryKey: string;
}) {
  const { act } = useRecoveryActions();
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState<Placement | null>(null);
  const [sentKey, setSentKey] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  // Where the button was when the menu opened. A menu is stale once it moves.
  const anchored = useRef<DOMRect | null>(null);
  const menuId = useId();
  // One click sends one command. The wait ends when the backend answers, or
  // after a while if it never does.
  const sending = sentKey === recoveryKey;
  useEffect(() => {
    if (!sending) return;
    const timer = window.setTimeout(() => setSentKey(null), SENT_RESET_MS);
    return () => window.clearTimeout(timer);
  }, [sending]);

  function retry() {
    if (sending || !choices.retry) return;
    const delivery = act("retry", failure);
    // A dropped command never left the browser, so the button stays usable.
    if (delivery?.state !== "dropped") setSentKey(recoveryKey);
  }

  function close(returnFocus: boolean) {
    setOpen(false);
    setPlacement(null);
    if (returnFocus) trigger.current?.focus();
  }

  useLayoutEffect(() => {
    if (!open || !trigger.current || !menu.current) return;
    const anchor = trigger.current.getBoundingClientRect();
    anchored.current = anchor;
    const height = menu.current.offsetHeight;
    const width = Math.min(MENU_WIDTH, window.innerWidth - 2 * VIEWPORT_MARGIN);
    const left = Math.max(
      VIEWPORT_MARGIN,
      Math.min(anchor.right - width, window.innerWidth - width - VIEWPORT_MARGIN),
    );
    const below = anchor.bottom + MENU_GAP;
    const fitsBelow = below + height <= window.innerHeight - VIEWPORT_MARGIN;
    setPlacement({ left, top: fitsBelow ? below : Math.max(VIEWPORT_MARGIN, anchor.top - MENU_GAP - height) });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    menu.current
      ?.querySelector<HTMLElement>('[role="menuitem"]')
      ?.focus({ preventScroll: true });
    const outside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!menu.current?.contains(target) && !trigger.current?.contains(target)) {
        close(false);
      }
    };
    // The menu is placed against the window, so it closes when the window
    // changes size or when a scroll moves the button away from it.
    const resized = () => close(false);
    const scrolled = () => {
      const from = anchored.current;
      const now = trigger.current?.getBoundingClientRect();
      if (!from || !now) return;
      if (Math.abs(now.top - from.top) > 2 || Math.abs(now.left - from.left) > 2) {
        close(false);
      }
    };
    document.addEventListener("mousedown", outside);
    window.addEventListener("resize", resized);
    window.addEventListener("scroll", scrolled, true);
    return () => {
      document.removeEventListener("mousedown", outside);
      window.removeEventListener("resize", resized);
      window.removeEventListener("scroll", scrolled, true);
    };
  }, [open]);

  function onMenuKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
      return;
    }
    if (event.key === "Tab") {
      close(false);
      return;
    }
    const items = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]'),
    );
    const at = items.indexOf(document.activeElement as HTMLElement);
    const move = (to: number) => {
      event.preventDefault();
      items[(to + items.length) % items.length]?.focus();
    };
    if (event.key === "ArrowDown") move(at + 1);
    else if (event.key === "ArrowUp") move(at - 1);
    else if (event.key === "Home") move(0);
    else if (event.key === "End") move(items.length - 1);
  }

  const retryName = choices.retry?.asksApproval
    ? `Retry ${label}. Asks for approval first.`
    : `Retry ${label}`;

  return (
    <div className="sourcecado-step-recover">
      {choices.retry ? (
        <button
          type="button"
          className="sourcecado-step-icon-button"
          aria-label={sending ? `Retrying ${label}` : retryName}
          aria-disabled={sending ? true : undefined}
          title={sending ? "Retrying" : retryName}
          onClick={retry}
        >
          {sending ? <StatusIcon state="running" /> : <ActionIcon name="retry" />}
        </button>
      ) : null}
      <button
        ref={trigger}
        type="button"
        className="sourcecado-step-icon-button"
        aria-label={`More recovery options for ${label}`}
        title="More options"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => (open ? close(true) : setOpen(true))}
      >
        <MoreIcon />
      </button>
      {open
        ? createPortal(
            <div
              ref={menu}
              id={menuId}
              role="menu"
              aria-label={`Recovery options for ${label}`}
              className="sourcecado-step-menu"
              style={
                placement
                  ? { top: placement.top, left: placement.left }
                  : { top: 0, left: 0, opacity: 0 }
              }
              onKeyDown={onMenuKeyDown}
            >
              {choices.retry ? (
                <MenuButton
                  onPick={() => {
                    close(true);
                    retry();
                  }}
                  disabled={sending}
                >
                  <MenuRow
                    icon={<ActionIcon name="retry" />}
                    title="Retry"
                    detail={
                      choices.retry.asksApproval
                        ? "Asks for approval first"
                        : "Run this step again"
                    }
                  />
                </MenuButton>
              ) : null}
              {choices.repairRoute ? (
                <a
                  role="menuitem"
                  className="sourcecado-menu-option"
                  href={choices.repairRoute}
                  onClick={() => {
                    act("repair", failure);
                    close(false);
                  }}
                >
                  <MenuRow
                    icon={<ActionIcon name="repair" />}
                    title="Repair connection"
                    detail={failure.source}
                  />
                </a>
              ) : null}
              {choices.continueWithout ? (
                <MenuButton
                  onPick={() => {
                    act("continue", failure);
                    close(true);
                  }}
                >
                  <MenuRow
                    icon={<ActionIcon name="continue" />}
                    title="Continue without this source"
                    detail={failure.source}
                  />
                </MenuButton>
              ) : null}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

function MenuButton({
  onPick,
  disabled = false,
  children,
}: {
  readonly onPick: () => void;
  readonly disabled?: boolean;
  readonly children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className="sourcecado-menu-option"
      aria-disabled={disabled ? true : undefined}
      onClick={disabled ? undefined : onPick}
    >
      {children}
    </button>
  );
}
