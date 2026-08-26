import { closeDb, getDb } from "@/lib/db";
import {
  failRun,
  failToolCall,
  finishRun,
  finishRunStep,
  finishToolCall,
  getRunTrace,
  skipRunStep,
  startRun,
  startRunStep,
  startToolCall,
} from "@/lib/ledger";
import { runMigrations } from "@/lib/migrate";

async function resetLedgerTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

describe("ledger helpers", () => {
  beforeEach(async () => {
    await resetLedgerTables();
  });

  afterEach(async () => {
    await closeDb();
  });

  it("creates, finishes, and fails runs", async () => {
    const db = getDb();
    const run = await startRun(db, {
      runType: "chat",
      title: "Answer sourcing question",
      input: { question: "Who should we contact?" },
    });

    expect(run).toMatchObject({
      runType: "chat",
      title: "Answer sourcing question",
      status: "running",
      input: { question: "Who should we contact?" },
    });

    const finished = await finishRun(db, {
      runId: run.id,
      output: { answer: "Start with alumni founders." },
    });
    expect(finished).toMatchObject({
      id: run.id,
      status: "succeeded",
      output: { answer: "Start with alumni founders." },
    });
    expect(finished.completedAt).toBeInstanceOf(Date);

    const failedRun = await startRun(db, { runType: "routine", title: "Failing run" });
    const failed = await failRun(db, {
      runId: failedRun.id,
      errorType: "provider_error",
      errorMessage: "Provider timed out.",
      error: { timeoutMs: 30_000 },
    });
    expect(failed).toMatchObject({
      id: failedRun.id,
      status: "failed",
      errorType: "provider_error",
      errorMessage: "Provider timed out.",
      error: { timeoutMs: 30_000 },
    });
  });

  it("creates nested steps, skips steps, and rejects cross-run parents", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "Nested run" });
    const otherRun = await startRun(db, { runType: "chat", title: "Other run" });
    const parent = await startRunStep(db, {
      runId: run.id,
      stepKind: "agent",
      name: "answer_question",
      input: { question: "Who is warm?" },
    });
    const child = await startRunStep(db, {
      runId: run.id,
      parentStepId: parent.id,
      stepKind: "retrieval",
      name: "retrieve_memory",
    });

    expect(child).toMatchObject({
      runId: run.id,
      parentStepId: parent.id,
      stepKind: "retrieval",
      status: "running",
    });

    const finishedChild = await finishRunStep(db, {
      runStepId: child.id,
      output: { chunks: 3 },
    });
    expect(finishedChild).toMatchObject({ id: child.id, status: "succeeded" });

    const skipped = await skipRunStep(db, {
      runStepId: parent.id,
      output: { reason: "No draft needed." },
    });
    expect(skipped).toMatchObject({ id: parent.id, status: "skipped" });

    await expect(
      startRunStep(db, {
        runId: otherRun.id,
        parentStepId: parent.id,
        stepKind: "model",
        name: "bad_parent",
      }),
    ).rejects.toThrow(/parentStepId belongs to run/);
  });

  it("creates succeeded and failed tool call lifecycles", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "Tool run" });
    const step = await startRunStep(db, {
      runId: run.id,
      stepKind: "tool",
      name: "apollo_search_contacts",
    });

    const toolCall = await startToolCall(db, {
      runId: run.id,
      runStepId: step.id,
      toolName: "apollo_search_contacts",
      arguments: { organization: "OpenAI" },
    });
    expect(toolCall).toMatchObject({
      runId: run.id,
      runStepId: step.id,
      toolName: "apollo_search_contacts",
      status: "running",
      arguments: { organization: "OpenAI" },
    });

    const finished = await finishToolCall(db, {
      toolCallId: toolCall.id,
      result: { contacts: 2 },
    });
    expect(finished).toMatchObject({ id: toolCall.id, status: "succeeded", result: { contacts: 2 } });

    const failedCall = await startToolCall(db, {
      runId: run.id,
      runStepId: step.id,
      toolName: "web_search",
    });
    const failed = await failToolCall(db, {
      toolCallId: failedCall.id,
      errorType: "quota_error",
      errorMessage: "Search quota exhausted.",
      error: { provider: "search" },
    });
    expect(failed).toMatchObject({
      id: failedCall.id,
      status: "failed",
      errorType: "quota_error",
      errorMessage: "Search quota exhausted.",
      error: { provider: "search" },
    });
  });

  it("rejects tool calls whose step belongs to a different run", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "Run" });
    const otherRun = await startRun(db, { runType: "chat", title: "Other run" });
    const step = await startRunStep(db, {
      runId: run.id,
      stepKind: "tool",
      name: "tool_step",
    });

    await expect(
      startToolCall(db, {
        runId: otherRun.id,
        runStepId: step.id,
        toolName: "bad_tool",
      }),
    ).rejects.toThrow(/runStepId belongs to run/);
  });

  it("returns a nested run trace with model and tool calls attached to steps", async () => {
    const db = getDb();
    const run = await startRun(db, { runType: "chat", title: "Trace run" });
    const parent = await startRunStep(db, {
      runId: run.id,
      stepKind: "agent",
      name: "answer_question",
    });
    const modelStep = await startRunStep(db, {
      runId: run.id,
      parentStepId: parent.id,
      stepKind: "model",
      name: "rank_sourcing_leads",
    });
    await db`
      INSERT INTO model_calls (
        run_id,
        run_step_id,
        task_name,
        prompt_version,
        prompt_hash,
        provider,
        model,
        call_kind,
        status,
        input_tokens,
        output_tokens,
        total_tokens
      )
      VALUES (
        ${run.id},
        ${modelStep.id},
        'rank_sourcing_leads',
        '1',
        'abc123',
        'deepseek',
        'deepseek-chat',
        'generate_text',
        'succeeded',
        10,
        20,
        30
      )
    `;
    const toolStep = await startRunStep(db, {
      runId: run.id,
      parentStepId: parent.id,
      stepKind: "tool",
      name: "apollo_search_contacts",
    });
    await startToolCall(db, {
      runId: run.id,
      runStepId: toolStep.id,
      toolName: "apollo_search_contacts",
      arguments: { organization: "OpenAI" },
    });

    const trace = await getRunTrace(db, run.id);

    expect(trace).toMatchObject({
      id: run.id,
      title: "Trace run",
      steps: [
        {
          id: parent.id,
          name: "answer_question",
          children: [
            {
              id: modelStep.id,
              name: "rank_sourcing_leads",
              modelCalls: [
                {
                  taskName: "rank_sourcing_leads",
                  inputTokens: 10,
                  outputTokens: 20,
                  totalTokens: 30,
                },
              ],
            },
            {
              id: toolStep.id,
              name: "apollo_search_contacts",
              toolCalls: [
                {
                  toolName: "apollo_search_contacts",
                  status: "running",
                  arguments: { organization: "OpenAI" },
                },
              ],
            },
          ],
        },
      ],
    });
  });
});
