import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DiagnosticBundle } from "../src/routes/DiagnosticBundle";

const api = vi.hoisted(() => ({
  previewDiagnosticBundle: vi.fn(),
  exportDiagnosticBundle: vi.fn(),
}));

vi.mock("../src/api", () => ({
  previewDiagnosticBundle: api.previewDiagnosticBundle,
  exportDiagnosticBundle: api.exportDiagnosticBundle,
}));

const PREVIEW = {
  bundle_version: 1,
  subject: { kind: "run", run_id: "run-9f21" },
  evidence_categories: [
    {
      id: "run",
      title: "Run lifecycle and timing",
      description: "Lifecycle codes, model attempts, tool calls, and how it ended.",
      included: true,
    },
    {
      id: "logs",
      title: "Redacted log records",
      description: "Structured event records projected onto a closed field list.",
      included: true,
    },
    {
      id: "health",
      title: "Bounded health history",
      description: "Recent runs by state, trigger, and outcome.",
      included: false,
    },
  ],
  excluded: ["Prompts, persona text, and message bodies.", "Raw home paths."],
  members: ["run.json", "logs.json"],
  counts: { log_records: 6, findings: 2, connectors: 5, runs_considered: 1 },
  run: {
    run_id: "run-9f21",
    state: "failed",
    outcome_status: "failed",
    finished_at: "2026-08-20T09:04:00+00:00",
  },
  state: { healthy: false, blocked: false },
  findings: [],
  log_records: [],
};

const BUNDLE = {
  bundle_id: "3f9c",
  generated_at: "2026-08-20T09:05:00+00:00",
  path: "<state>/diagnostics/sourcecado-diagnostic-3f9c.zip",
  sha256: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  size_bytes: 20480,
  members: ["run.json", "logs.json"],
};

function start(runId = "run-9f21") {
  render(<DiagnosticBundle />);
  fireEvent.change(screen.getByLabelText("Run ID"), { target: { value: runId } });
  fireEvent.click(screen.getByRole("button", { name: "Review bundle" }));
}

describe("DiagnosticBundle", () => {
  beforeEach(() => {
    api.previewDiagnosticBundle.mockReset();
    api.exportDiagnosticBundle.mockReset();
  });

  it("reviews the bundle before anything is saved, then saves only when asked", async () => {
    api.previewDiagnosticBundle.mockResolvedValue({ status: "ok", value: PREVIEW });
    api.exportDiagnosticBundle.mockResolvedValue({ status: "ok", value: BUNDLE });

    start();

    expect(await screen.findByText("Run lifecycle and timing")).toBeInTheDocument();
    expect(screen.getByText("Redacted log records")).toBeInTheDocument();
    expect(screen.getByText("Not in this bundle")).toBeInTheDocument();
    expect(
      screen.getByText("Prompts, persona text, and message bodies."),
    ).toBeInTheDocument();
    expect(api.exportDiagnosticBundle).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Save bundle to this Mac" }));

    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(screen.getByText(BUNDLE.path)).toBeInTheDocument();
    expect(screen.getByText("20 KB", { exact: false })).toBeInTheDocument();
    expect(api.exportDiagnosticBundle).toHaveBeenCalledWith({ run_id: "run-9f21" });
    expect(
      screen.queryByRole("button", { name: "Save bundle to this Mac" }),
    ).toBeNull();
  });

  it("reports a refused scan by category and location, and saves nothing", async () => {
    api.previewDiagnosticBundle.mockResolvedValue({
      status: "refused",
      matches: [
        { category: "registered_secret", location: "connectors.json.connectors.0.title" },
      ],
    });

    start();

    expect(await screen.findByText("Nothing was written.")).toBeInTheDocument();
    expect(screen.getByText("registered_secret")).toBeInTheDocument();
    expect(
      screen.getByText("connectors.json.connectors.0.title"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save bundle to this Mac" }),
    ).toBeNull();
    expect(api.exportDiagnosticBundle).not.toHaveBeenCalled();
  });

  it("reports a refusal raised at save time and leaves no saved path on screen", async () => {
    api.previewDiagnosticBundle.mockResolvedValue({ status: "ok", value: PREVIEW });
    api.exportDiagnosticBundle.mockResolvedValue({
      status: "refused",
      matches: [{ category: "issued_credential", location: "logs.json.records.4" }],
    });

    start();
    fireEvent.click(await screen.findByRole("button", { name: "Save bundle to this Mac" }));

    expect(await screen.findByText("Nothing was written.")).toBeInTheDocument();
    expect(screen.getByText("logs.json.records.4")).toBeInTheDocument();
    expect(screen.queryByText("Saved")).toBeNull();
  });

  it("cannot start a review without a run", () => {
    render(<DiagnosticBundle />);
    expect(screen.getByRole("button", { name: "Review bundle" })).toBeDisabled();
  });

  it("reports a failed request without claiming anything was saved", async () => {
    api.previewDiagnosticBundle.mockRejectedValue(new Error("diagnostics 500"));

    start();

    await waitFor(() =>
      expect(screen.getByText("The bundle could not be prepared")).toBeInTheDocument(),
    );
    expect(screen.queryByText("Saved")).toBeNull();
  });
});
