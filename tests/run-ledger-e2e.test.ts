import { parseLlmCandidates } from "@/extractors/llm";
import { closeDb, getDb } from "@/lib/db";
import {
  finishRun,
  finishRunStep,
  finishToolCall,
  getRunTrace,
  startRun,
  startRunStep,
  startToolCall,
} from "@/lib/ledger";
import { callModel, type ModelGatewayProvider } from "@/lib/model-gateway";
import { runMigrations } from "@/lib/migrate";
import { z } from "zod";

async function resetLedgerTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

describe("F3/F4 run ledger integration", () => {
  beforeEach(async () => {
    await resetLedgerTables();
  });

  afterEach(async () => {
    await closeDb();
  });

  it("records a review-ready sourcing trace across tool, model, and embedding steps", async () => {
    const db = getDb();
    const run = await startRun(db, {
      runType: "weekly_sourcing_loop",
      title: "Codeology alumni sourcing",
      input: { routineId: "routine-codeology-ai", cohort: "alumni" },
    });
    const agentStep = await startRunStep(db, {
      runId: run.id,
      stepKind: "agent",
      name: "weekly_sourcing_loop",
      input: { goal: "Find warm AI alumni leads." },
    });

    const apolloStep = await startRunStep(db, {
      runId: run.id,
      parentStepId: agentStep.id,
      stepKind: "tool",
      name: "apollo_search_contacts",
    });
    const apolloCall = await startToolCall(db, {
      runId: run.id,
      runStepId: apolloStep.id,
      toolName: "apollo.searchContacts",
      arguments: { query: "Codeology alumni AI founder" },
      metadata: { ledgerCategory: "credits" },
    });
    await finishToolCall(db, {
      toolCallId: apolloCall.id,
      result: {
        creditsUsed: 1,
        contacts: [{ name: "Ada Lovelace", company: "Analytical Labs" }],
      },
    });
    await finishRunStep(db, {
      runStepId: apolloStep.id,
      output: { contactsFound: 1 },
    });

    const extracted = {
      candidates: [
        {
          kind: "entity",
          subject: "Ada Lovelace",
          entityType: "person",
          confidence: 0.93,
          evidenceText: "Ada Lovelace founded Analytical Labs.",
        },
        {
          kind: "relationship",
          subject: "Ada Lovelace",
          object: "Analytical Labs",
          relationshipType: "works_at",
          confidence: 0.82,
          evidenceText: "Ada Lovelace founded Analytical Labs.",
        },
      ],
    };
    const extractionProvider = vi.fn<ModelGatewayProvider>().mockResolvedValue({
      object: extracted,
      usage: { inputTokens: 120, outputTokens: 60, totalTokens: 180 },
      rawResponse: { id: "fake-extraction-response" },
    });
    const extractionResult = await callModel<{ candidates: unknown[] }>(db, {
      kind: "generate_object",
      taskName: "extract_memory_candidates",
      promptVersion: "1",
      prompt: [
        "Source type: text",
        "Source path: apollo://contacts/ada-lovelace",
        "",
        "Ada Lovelace founded Analytical Labs.",
      ].join("\n"),
      system: "Extract Sourcecado memory candidates.",
      schema: z.object({ candidates: z.array(z.unknown()) }),
      schemaName: "sourcyavo_memory_candidates",
      trace: { runId: run.id, parentStepId: agentStep.id },
      providerName: "fake",
      model: "fake-generation-model",
      provider: extractionProvider,
    });
    const candidates = parseLlmCandidates(JSON.stringify(extractionResult.object));

    const embeddingProvider = vi.fn<ModelGatewayProvider>().mockResolvedValue({
      embedding: Array.from({ length: 1536 }, (_, index) => (index === 0 ? 0.42 : 0)),
      usage: { tokens: 32 },
      rawResponse: { id: "fake-embedding-response" },
    });
    const embeddingResult = await callModel(db, {
      kind: "embed",
      taskName: "embed_memory_candidate",
      promptVersion: "1",
      value: candidates[0].evidenceText,
      trace: { runId: run.id, parentStepId: agentStep.id },
      providerName: "fake",
      model: "fake-embedding-model",
      provider: embeddingProvider,
    });

    await finishRunStep(db, {
      runStepId: agentStep.id,
      output: {
        candidates: candidates.length,
        extractionModelCallId: extractionResult.modelCallId,
        embeddingModelCallId: embeddingResult.modelCallId,
      },
    });
    await finishRun(db, {
      runId: run.id,
      output: { status: "review_ready", candidates: candidates.length },
    });

    const trace = await getRunTrace(db, run.id);
    expect(trace).toMatchObject({
      id: run.id,
      runType: "weekly_sourcing_loop",
      status: "succeeded",
      output: { status: "review_ready", candidates: 2 },
    });
    expect(candidates).toHaveLength(2);

    const rootStep = trace?.steps[0];
    expect(rootStep).toMatchObject({
      id: agentStep.id,
      stepKind: "agent",
      name: "weekly_sourcing_loop",
      status: "succeeded",
    });

    const toolStep = rootStep?.children.find((step) => step.name === "apollo_search_contacts");
    expect(toolStep).toMatchObject({
      stepKind: "tool",
      status: "succeeded",
      output: { contactsFound: 1 },
    });
    expect(toolStep?.toolCalls[0]).toMatchObject({
      toolName: "apollo.searchContacts",
      status: "succeeded",
      result: { creditsUsed: 1 },
    });

    const extractionStep = rootStep?.children.find((step) => step.name === "extract_memory_candidates");
    expect(extractionStep).toMatchObject({
      stepKind: "model",
      status: "succeeded",
    });
    expect(extractionStep?.modelCalls[0]).toMatchObject({
      taskName: "extract_memory_candidates",
      promptVersion: "1",
      provider: "fake",
      model: "fake-generation-model",
      callKind: "generate_object",
      status: "succeeded",
      inputTokens: 120,
      outputTokens: 60,
      totalTokens: 180,
    });

    const embeddingStep = rootStep?.children.find((step) => step.name === "embed_memory_candidate");
    expect(embeddingStep).toMatchObject({
      stepKind: "embedding",
      status: "succeeded",
    });
    expect(embeddingStep?.modelCalls[0]).toMatchObject({
      taskName: "embed_memory_candidate",
      callKind: "embed",
      embeddingDimensions: 1536,
      inputTokens: 32,
      totalTokens: 32,
    });

    expect(extractionProvider).toHaveBeenCalledTimes(1);
    expect(embeddingProvider).toHaveBeenCalledTimes(1);
  });
});
