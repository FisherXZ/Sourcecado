export const RAIL_COLLAPSED_KEY = "sourcecado.shell.rail-collapsed.v1";

// Whether the operator chose the 48px icon column. Only a stored "1" counts;
// a missing, unreadable or unexpected value means the full rail.
export function readRailCollapsed(): boolean {
  try {
    return window.localStorage.getItem(RAIL_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeRailCollapsed(collapsed: boolean) {
  try {
    window.localStorage.setItem(RAIL_COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch {
    // A full or unavailable store must never block the toggle itself.
  }
}
