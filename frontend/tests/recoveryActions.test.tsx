import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { ToolCallMessagePart } from "@assistant-ui/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActivityGroup } from "../src/chat/ActivityGroup";
import type { ToolFailure } from "../src/chat/protocol";
import { RecoveryProvider } from "../src/chat/recovery";

const failure = (overrides: Partial<ToolFailure> = {}): ToolFailure => ({
  class: "timeout_network",
  connector_id: "drive",
  source: "Google Drive",
  retry_safe: true,
  idempotent: true,
  summary: "Google Drive could not be reached. Retry is safe.",
  repair_route: null,
  detail: "PRIVATE_DETAIL",
  call_id: "call-1",
  run_id: "run-1",
  session_id: "thread-1",
  state: "failed",
  ...overrides,
});

function failedStep(
  fail: ToolFailure,
  recovery?: Record<string, unknown>,
  overrides: Partial<ToolCallMessagePart> = {},
): ToolCallMessagePart {
  return {
    type: "tool-call",
    toolCallId: "call-1",
    toolName: "drive_search",
    args: { query: "Codeology" },
    argsText: "{}",
    result: { error: "PRIVATE_DETAIL" },
    isError: true,
    providerMetadata: { sourcecado: { failure: fail, recovery: recovery ?? null } },
    ...overrides,
  };
}

function renderStep(
  step: ToolCallMessagePart,
  onAction = vi.fn(),
  messageState = "complete",
) {
  const view = render(
    <RecoveryProvider onAction={onAction}>
      <ActivityGroup tools={[step]} messageState={messageState} />
    </RecoveryProvider>,
  );
  return { ...view, onAction };
}

const retryButton = () => screen.queryByRole("button", { name: /^Retry / });
const overflow = () =>
  screen.getByRole("button", { name: /^More recovery options for/ });

afterEach(() => {
  vi.useRealTimers();
});

describe("failed step row", () => {
  it("shows an x, a short reason and a Retry icon button on the row", () => {
    renderStep(failedStep(failure()));

    const row = screen.getByRole("button", { name: /^Searching Drive/ });
    expect(row).toHaveTextContent("Could not connect");
    // The word Failed stays in the accessible name for people who cannot see the icon.
    expect(row).toHaveTextContent("Failed: Could not connect");
    expect(row.querySelector(".sourcecado-status-icon-failed")).not.toBeNull();

    const retry = screen.getByRole("button", {
      name: "Retry Searching Drive for “Codeology”",
    });
    // An icon button: no visible text, one icon.
    expect(retry).toHaveTextContent("");
    expect(retry.querySelector("svg")).not.toBeNull();
    // The failure details still wait behind the disclosure.
    expect(document.body).not.toHaveTextContent("PRIVATE_DETAIL");
  });

  it("sends the retry command once, then waits for the backend to answer", () => {
    const { onAction, rerender } = renderStep(failedStep(failure()));

    fireEvent.click(retryButton()!);
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith("retry", expect.objectContaining({ call_id: "call-1" }));

    const waiting = screen.getByRole("button", { name: /^Retrying Searching Drive/ });
    expect(waiting).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(waiting);
    expect(onAction).toHaveBeenCalledTimes(1);

    // The backend answers: the retry failed. The button is ready again.
    rerender(
      <RecoveryProvider onAction={onAction}>
        <ActivityGroup
          tools={[
            failedStep(failure(), {
              commandId: "cmd-1",
              status: "failed",
              outcome: "Retry failed; successful prior work was preserved.",
            }),
          ]}
          messageState="complete"
        />
      </RecoveryProvider>,
    );
    expect(retryButton()).not.toHaveAttribute("aria-disabled");
    expect(
      screen.getByText("Retry failed; successful prior work was preserved."),
    ).toBeInTheDocument();
  });

  it("keeps Retry usable when the command never left the browser", () => {
    const onAction = vi.fn(() => ({ state: "dropped", reason: "offline" }) as const);
    renderStep(failedStep(failure()), onAction);

    fireEvent.click(retryButton()!);
    fireEvent.click(retryButton()!);
    expect(onAction).toHaveBeenCalledTimes(2);
  });

  it("lets a click that no event answers be sent again after a wait", () => {
    vi.useFakeTimers();
    const { onAction } = renderStep(failedStep(failure()));

    fireEvent.click(retryButton()!);
    expect(retryButton()).toBeNull();
    act(() => {
      vi.advanceTimersByTime(16_000);
    });
    fireEvent.click(retryButton()!);
    expect(onAction).toHaveBeenCalledTimes(2);
  });

  it("names the failure class in a few words", () => {
    const cases: [Partial<ToolFailure>, string][] = [
      [{ class: "connector_auth", repair_route: "#/connections/drive" }, "Needs repair"],
      [{ class: "timeout_network" }, "Could not connect"],
      [{ class: "validation" }, "Request rejected"],
      [{ class: "unknown" }, "Failed"],
    ];
    for (const [overrides, reason] of cases) {
      const { unmount } = renderStep(failedStep(failure(overrides)));
      expect(screen.getByRole("button", { name: /^Searching Drive/ })).toHaveTextContent(reason);
      unmount();
    }
  });
});

describe("failed step overflow menu", () => {
  it("holds Retry, Repair connection and Continue without this source, each with an icon", () => {
    const { onAction } = renderStep(
      failedStep(
        failure({
          class: "connector_auth",
          repair_route: "#/connections/drive",
          summary: "Google Drive needs to be repaired before this source can be checked.",
        }),
      ),
    );

    fireEvent.click(overflow());
    const menu = screen.getByRole("menu", { name: /^Recovery options for/ });
    const items = within(menu).getAllByRole("menuitem");
    expect(items.map((item) => item.textContent)).toEqual([
      "RetryRun this step again",
      "Repair connectionGoogle Drive",
      "Continue without this sourceGoogle Drive",
    ]);
    for (const item of items) expect(item.querySelector("svg")).not.toBeNull();
    expect(within(menu).getByRole("menuitem", { name: /Repair connection/ })).toHaveAttribute(
      "href",
      "#/connections/drive",
    );

    // Every pick runs the existing command, then closes the menu.
    fireEvent.click(within(menu).getByRole("menuitem", { name: /Repair connection/ }));
    expect(onAction).toHaveBeenLastCalledWith("repair", expect.objectContaining({ source: "Google Drive" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    fireEvent.click(overflow());
    fireEvent.click(screen.getByRole("menuitem", { name: /Continue without this source/ }));
    expect(onAction).toHaveBeenLastCalledWith("continue", expect.objectContaining({ source: "Google Drive" }));
  });

  it("offers Repair only where the backend names a repair route", () => {
    renderStep(failedStep(failure({ repair_route: null })));

    fireEvent.click(overflow());
    expect(screen.queryByRole("menuitem", { name: /Repair connection/ })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /^Retry/ })).toBeInTheDocument();
  });

  it("runs Retry from the menu through the same command", () => {
    const { onAction } = renderStep(failedStep(failure()));

    fireEvent.click(overflow());
    fireEvent.click(screen.getByRole("menuitem", { name: /^Retry/ }));
    expect(onAction).toHaveBeenCalledWith("retry", expect.objectContaining({ call_id: "call-1" }));
  });

  it("opens outside the scrolling step list, so the list cannot clip it", () => {
    const { container } = renderStep(failedStep(failure()));

    fireEvent.click(overflow());
    const menu = screen.getByRole("menu");
    expect(container.contains(menu)).toBe(false);
    expect(menu.parentElement).toBe(document.body);
  });

  it("is a keyboard menu: focus enters it, arrows move, Escape closes and returns focus", () => {
    renderStep(failedStep(failure({ class: "connector_auth", repair_route: "#/connections/drive" })));

    const trigger = overflow();
    trigger.focus();
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const items = screen.getAllByRole("menuitem");
    expect(items[0]).toHaveFocus();

    const menu = screen.getByRole("menu");
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(items[1]).toHaveFocus();
    fireEvent.keyDown(menu, { key: "End" });
    expect(items[2]).toHaveFocus();
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(items[0]).toHaveFocus();

    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("closes on a click outside", () => {
    renderStep(failedStep(failure()));

    fireEvent.click(overflow());
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});

describe("which recovery actions appear", () => {
  it("shows no Retry for a refusal by policy, and the menu holds Continue only", () => {
    renderStep(
      failedStep(
        failure({
          class: "permission",
          retry_safe: false,
          summary: "Board was not allowed to complete this action.",
        }),
      ),
    );

    expect(screen.getByRole("button", { name: /Not allowed/ })).toBeInTheDocument();
    expect(retryButton()).toBeNull();
    fireEvent.click(overflow());
    expect(screen.getAllByRole("menuitem")).toHaveLength(1);
    expect(screen.getByRole("menuitem", { name: /Continue without this source/ })).toBeInTheDocument();
  });

  it("shows no Retry for a request the tool rejected, because it would fail the same way", () => {
    renderStep(failedStep(failure({ class: "validation", summary: "Drive needs corrected request details before retrying." })));

    expect(retryButton()).toBeNull();
    fireEvent.click(overflow());
    expect(screen.queryByRole("menuitem", { name: /^Retry/ })).not.toBeInTheDocument();
  });

  it("says that a retry of a tool that is not retry-safe asks for approval first", () => {
    renderStep(
      failedStep(failure({ class: "unknown", retry_safe: false, idempotent: false, source: "Gmail", summary: "Gmail failed. Review the details before choosing what to do next." }), undefined, {
        toolName: "gmail_send",
      }),
    );

    expect(
      screen.getByRole("button", { name: /^Retry Sending Gmail draft\. Asks for approval first\./ }),
    ).toBeInTheDocument();
    // The step opens itself, and the same fact is on the page as text.
    expect(screen.getByText("Retry asks for approval first.")).toBeInTheDocument();
    fireEvent.click(overflow());
    expect(screen.getByRole("menuitem", { name: /^Retry/ })).toHaveTextContent("Asks for approval first");
  });

  it("does not offer a second Retry while a retry approval is waiting", () => {
    renderStep(
      failedStep(failure({ retry_safe: false }), {
        commandId: "cmd-1",
        status: "approval_required",
        outcome: "Unsafe retry requires a fresh approval.",
      }),
    );

    expect(retryButton()).toBeNull();
    expect(screen.getByText("Unsafe retry requires a fresh approval.")).toBeInTheDocument();
    fireEvent.click(overflow());
    expect(screen.queryByRole("menuitem", { name: /^Retry/ })).not.toBeInTheDocument();
  });

  it("offers nothing once the backend has settled the step", () => {
    for (const status of ["succeeded", "skipped", "denied", "interrupted"]) {
      const { unmount } = renderStep(failedStep(failure(), { commandId: "cmd-1", status }));
      expect(retryButton()).toBeNull();
      expect(screen.queryByRole("button", { name: /More recovery options/ })).not.toBeInTheDocument();
      unmount();
    }
  });

  it("keeps Retry after the operator opens the repair page, until the step is retried", () => {
    renderStep(
      failedStep(failure({ class: "connector_auth", repair_route: "#/connections/drive" }), {
        commandId: "cmd-2",
        status: "awaiting_repair",
      }),
    );

    expect(retryButton()).not.toBeNull();
  });

  it("never offers Retry on a step whose outcome is unknown or that was interrupted", () => {
    renderStep(
      failedStep(failure(), undefined, {
        result: undefined,
        isError: false,
        providerMetadata: undefined,
      }),
    );

    expect(screen.getByRole("button", { name: /Outcome unknown/ })).toBeInTheDocument();
    expect(retryButton()).toBeNull();
    expect(screen.queryByRole("button", { name: /More recovery options/ })).not.toBeInTheDocument();
  });
});
