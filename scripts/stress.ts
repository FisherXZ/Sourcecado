import { runStressHarness, type StressReport } from "../tests/stress/harness.js";

// Thin CLI over runStressHarness. Prints a human-readable report by default, or
// the raw StressReport JSON with --json. Exit code is non-zero ONLY on a
// harness-internal failure (a thrown error). Classified benchmark misses are a
// normal, expected output and do NOT fail the process.
async function main(): Promise<void> {
  const asJson = process.argv.includes("--json");
  const report = await runStressHarness();

  if (asJson) {
    process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
    return;
  }

  process.stdout.write(`${formatReport(report)}\n`);
}

function formatReport(report: StressReport): string {
  const lines: string[] = [];
  lines.push("Sourcyavo Stress Harness Report");
  lines.push("===============================");
  lines.push(`Snapshot: ${report.snapshotPath}`);
  lines.push(`Permission evaluation: ${report.permissionState}`);
  lines.push("");
  lines.push(`Ingest: ${report.ingest.processed} processed, ${report.ingest.skipped} skipped`);
  lines.push(
    `Refresh: ${report.refresh.extracted} extracted, ${report.refresh.reused} reused, ${report.refresh.failed} failed (${report.refresh.chunksProcessed} chunks)`
  );
  lines.push("");

  lines.push(`Skipped files (${report.skippedFiles.length}):`);
  if (report.skippedFiles.length === 0) {
    lines.push("  (none)");
  } else {
    for (const skipped of report.skippedFiles) {
      lines.push(`  - ${basename(skipped.path)} [${skipped.category}]`);
    }
  }
  lines.push("");

  lines.push(`Extraction failures (${report.extractionFailures.length}):`);
  if (report.extractionFailures.length === 0) {
    lines.push("  (none)");
  } else {
    for (const failure of report.extractionFailures) {
      lines.push(`  - chunk ${failure.chunkId ?? "?"}: ${failure.error}`);
    }
  }
  lines.push("");

  lines.push(`Cases (${report.passCount} pass / ${report.missCount} miss):`);
  for (const result of report.cases) {
    const tag = result.outcome === "pass" ? "PASS" : `MISS:${result.category}`;
    lines.push(`  [${tag}] ${result.id} — ${result.detail}`);
  }
  lines.push("");

  lines.push("Miss tally:");
  for (const [category, count] of Object.entries(report.tally)) {
    const note = category === "temporal" ? " (deferred — Checkpoint 4)" : "";
    lines.push(`  ${category}: ${count}${note}`);
  }

  return lines.join("\n");
}

function basename(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

main().catch((error: unknown) => {
  process.stderr.write(`Stress harness failed: ${error instanceof Error ? error.stack : String(error)}\n`);
  process.exitCode = 1;
});
