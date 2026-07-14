import { createHash } from "node:crypto";
import { createAnthropic } from "@ai-sdk/anthropic";
import { deepseek } from "@ai-sdk/deepseek";
import { openai } from "@ai-sdk/openai";
import type { LanguageModel } from "ai";
import { embed, embedMany, generateObject, generateText } from "ai";
import type postgres from "postgres";
import type { z } from "zod";
import { failRunStep, finishRunStep, startRunStep } from "./ledger";
import { anthropicAdapter } from "./llm/anthropic";
import { createOpenAiCompatAdapter } from "./llm/openai-compat";
import type {
  LlmAdapter,
  LlmAssistantBlock,
  LlmAssistantMessage,
  LlmMessage,
  LlmStreamEvent,
  LlmToolDefinition,
  LlmUsage,
  StopReason,
} from "./llm/types";

export type ModelCallKind = "generate_text" | "generate_object" | "embed" | "embed_many" | "stream_turn";
export type RedactionMode = "none" | "suppress";

export interface ModelGatewayTrace {
  runId: number;
  parentStepId?: number | null;
}

export interface ModelGatewayProviderRequest {
  kind: ModelCallKind;
  prompt?: string;
  system?: string;
  value?: string;
  values?: string[];
  schemaName?: string;
  taskName: string;
  promptVersion: string;
  providerName: string;
  model: string;
}

export interface ModelGatewayProviderResult {
  text?: string;
  object?: unknown;
  embedding?: number[];
  embeddings?: number[][];
  usage?: unknown;
  rawResponse?: unknown;
}

export type ModelGatewayProvider = (
  request: ModelGatewayProviderRequest,
) => Promise<ModelGatewayProviderResult>;

interface BaseCallModelInput {
  taskName: string;
  promptVersion: string;
  providerName?: string;
  model?: string;
  trace?: ModelGatewayTrace;
  capturePayloads?: boolean;
  redactionMode?: RedactionMode;
  provider?: ModelGatewayProvider;
  metadata?: unknown;
}

export type GenerateTextInput = BaseCallModelInput & {
  kind: "generate_text";
  prompt: string;
  system?: string;
};

export type GenerateObjectInput<TSchema extends z.ZodType = z.ZodType> = BaseCallModelInput & {
  kind: "generate_object";
  prompt: string;
  system?: string;
  schema: TSchema;
  schemaName?: string;
};

export type EmbedInput = BaseCallModelInput & {
  kind: "embed";
  value: string;
};

export type EmbedManyInput = BaseCallModelInput & {
  kind: "embed_many";
  values: string[];
};

export type CallModelInput = GenerateTextInput | GenerateObjectInput | EmbedInput | EmbedManyInput;

export interface CallModelResult<TObject = unknown> {
  kind: ModelCallKind;
  modelCallId: number;
  runStepId: number | null;
  providerName: string;
  model: string;
  text?: string;
  object?: TObject;
  embedding?: number[];
  embeddings?: number[][];
  usage: NormalizedUsage;
}

export interface NormalizedUsage {
  raw: unknown;
  inputTokens: number | null;
  outputTokens: number | null;
  totalTokens: number | null;
}

export class ModelGatewayError extends Error {
  readonly code: string;
  override readonly cause?: unknown;

  constructor(code: string, message: string, options: { cause?: unknown } = {}) {
    super(message);
    this.name = "ModelGatewayError";
    this.code = code;
    this.cause = options.cause;
  }
}

type Sql = postgres.Sql;

export async function callModel<TObject = unknown>(
  db: Sql,
  input: CallModelInput,
): Promise<CallModelResult<TObject>> {
  const providerName = resolveProviderName(input);
  const model = resolveModel(input);
  const capturePayloads = input.capturePayloads !== false && input.redactionMode !== "suppress";
  const requestPayload = buildRequestPayload(input);
  const promptHash = hashPromptPayload(input);
  const runStep = input.trace
    ? await startRunStep(db, {
        runId: input.trace.runId,
        parentStepId: input.trace.parentStepId ?? null,
        stepKind: input.kind === "embed" || input.kind === "embed_many" ? "embedding" : "model",
        name: input.taskName,
        input: capturePayloads ? requestPayload : undefined,
        metadata: input.metadata,
      })
    : null;

  let modelCallId: number | null = null;
  let recordedSuccess = false;

  try {
    const [modelCall] = await db`
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
        request_json
      )
      VALUES (
        ${input.trace?.runId ?? null},
        ${runStep?.id ?? null},
        ${input.taskName},
        ${input.promptVersion},
        ${promptHash},
        ${providerName},
        ${model},
        ${input.kind},
        'running',
        ${capturePayloads ? toJson(db, requestPayload) : null}
      )
      RETURNING id
    `;
    modelCallId = Number(modelCall.id);

    const providerResult = await executeProvider(input, providerName, model);
    const output = validateAndBuildOutput<TObject>(input, providerResult);
    const usage = normalizeUsage(providerResult.usage);
    const embeddingDimensions = getEmbeddingDimensions(input.kind, output);
    const responsePayload = buildResponsePayload(input.kind, output, providerResult.rawResponse);

    await db`
      UPDATE model_calls
      SET status = 'succeeded',
          response_json = ${capturePayloads ? toJson(db, responsePayload) : null},
          usage_json = ${usage.raw === null ? null : toJson(db, usage.raw)},
          input_tokens = ${usage.inputTokens},
          output_tokens = ${usage.outputTokens},
          total_tokens = ${usage.totalTokens},
          embedding_dimensions = ${embeddingDimensions},
          completed_at = now(),
          updated_at = now()
      WHERE id = ${modelCallId}
    `;
    recordedSuccess = true;

    if (runStep) {
      await finishRunStep(db, {
        runStepId: runStep.id,
        output: capturePayloads ? responsePayload : undefined,
      });
    }

    return {
      ...output,
      kind: input.kind,
      modelCallId,
      runStepId: runStep?.id ?? null,
      providerName,
      model,
      usage,
    };
  } catch (error) {
    // A failure after the model call was already recorded as succeeded is a
    // ledger bookkeeping error (e.g. finishRunStep), not a provider failure.
    // Rewriting the succeeded row to 'failed' would corrupt the trace and make
    // callers retry a call that already succeeded — surface the error instead.
    if (recordedSuccess) {
      throw error;
    }

    const gatewayError =
      error instanceof ModelGatewayError
        ? error
        : new ModelGatewayError("provider_error", errorMessage(error), { cause: error });
    const errorJson = serializeError(gatewayError.cause ?? gatewayError);

    // modelCallId is null only when the INSERT itself failed; there is no row
    // to mark failed, but the run step (if any) still needs to be closed out.
    if (modelCallId !== null) {
      await db`
        UPDATE model_calls
        SET status = 'failed',
            error_type = ${gatewayError.code},
            error_message = ${gatewayError.message},
            error_json = ${toJson(db, errorJson)},
            completed_at = now(),
            updated_at = now()
        WHERE id = ${modelCallId}
      `;
    }

    if (runStep) {
      await failRunStep(db, {
        runStepId: runStep.id,
        errorType: gatewayError.code,
        errorMessage: gatewayError.message,
        error: errorJson,
      });
    }

    throw gatewayError;
  }
}

async function executeProvider(
  input: CallModelInput,
  providerName: string,
  model: string,
): Promise<ModelGatewayProviderResult> {
  const request = toProviderRequest(input, providerName, model);
  if (input.provider) {
    return input.provider(request);
  }

  return executeDefaultProvider(input, providerName, model);
}

// Resolve the AI SDK language model for text/object generation. Embeddings stay
// on OpenAI. Keeps the gateway's single provider abstraction (ADR-0004) uniform
// across deepseek/anthropic without leaking provider details to callers.
// @ai-sdk/anthropic expects a base URL that includes the `/v1` segment and posts
// to `${baseURL}/messages`. Some environments export ANTHROPIC_BASE_URL as the
// bare host (the official SDK's convention, which appends /v1 itself), which 404s
// here. Normalize: honor a configured base but ensure it carries a version path.
export function resolveAnthropicBaseUrl(raw?: string): string {
  const configured = raw?.trim();
  if (!configured) return "https://api.anthropic.com/v1";
  const trimmed = configured.replace(/\/+$/, "");
  return /\/v\d+$/.test(trimmed) ? trimmed : `${trimmed}/v1`;
}

function generationModel(providerName: string, model: string): LanguageModel {
  if (providerName === "anthropic") {
    requireEnv("ANTHROPIC_API_KEY");
    const provider = createAnthropic({
      baseURL: resolveAnthropicBaseUrl(process.env.ANTHROPIC_BASE_URL),
    });
    return provider(model);
  }
  if (providerName !== "deepseek") {
    throw new ModelGatewayError(
      "config_error",
      `Unsupported generation provider: ${providerName}. Set SOURCECADO_GENERATION_PROVIDER to "anthropic" or "deepseek".`,
    );
  }
  requireEnv("DEEPSEEK_API_KEY");
  return deepseek(model);
}

async function executeDefaultProvider(
  input: CallModelInput,
  providerName: string,
  model: string,
): Promise<ModelGatewayProviderResult> {
  switch (input.kind) {
    case "generate_text": {
      const result = await generateText({
        model: generationModel(providerName, model),
        prompt: input.prompt,
        system: input.system,
      });
      return {
        text: result.text,
        usage: result.totalUsage ?? result.usage,
        rawResponse: result.response.body ?? result.response,
      };
    }
    case "generate_object": {
      const result = await generateObject({
        model: generationModel(providerName, model),
        prompt: input.prompt,
        system: input.system,
        schema: input.schema,
        schemaName: input.schemaName,
      });
      return {
        object: result.object,
        usage: result.usage,
        rawResponse: result.response.body ?? result.response,
      };
    }
    case "embed": {
      requireEnv("OPENAI_API_KEY");
      const result = await embed({
        model: openai.embedding(model),
        value: input.value,
      });
      return {
        embedding: result.embedding,
        usage: result.usage,
        rawResponse: result.response?.body ?? result.response,
      };
    }
    case "embed_many": {
      requireEnv("OPENAI_API_KEY");
      const result = await embedMany({
        model: openai.embedding(model),
        values: input.values,
      });
      return {
        embeddings: result.embeddings,
        usage: result.usage,
        rawResponse: result.responses,
      };
    }
  }
}

function validateAndBuildOutput<TObject>(
  input: CallModelInput,
  result: ModelGatewayProviderResult,
): Pick<CallModelResult<TObject>, "text" | "object" | "embedding" | "embeddings"> {
  switch (input.kind) {
    case "generate_text":
      if (typeof result.text !== "string") {
        throw new ModelGatewayError("invalid_output", "Model provider did not return generated text.");
      }
      return { text: result.text };
    case "generate_object": {
      const parsed = input.schema.safeParse(result.object);
      if (!parsed.success) {
        throw new ModelGatewayError("schema_error", "Model provider returned invalid structured output.", {
          cause: parsed.error,
        });
      }
      return { object: parsed.data as TObject };
    }
    case "embed":
      if (!Array.isArray(result.embedding)) {
        throw new ModelGatewayError("invalid_output", "Embedding provider did not return an embedding.");
      }
      return { embedding: result.embedding };
    case "embed_many":
      if (!Array.isArray(result.embeddings) || !result.embeddings.every(Array.isArray)) {
        throw new ModelGatewayError("invalid_output", "Embedding provider did not return embeddings.");
      }
      return { embeddings: result.embeddings };
  }
}

// Widened supertype of CallModelInput so streamAgentTurn can reuse both
// resolvers — they only inspect kind/providerName/model.
interface ProviderResolutionInput {
  kind: string;
  providerName?: string;
  model?: string;
}

export function resolveProviderName(input: ProviderResolutionInput): string {
  if (input.providerName?.trim()) {
    return input.providerName;
  }
  if (input.kind === "embed" || input.kind === "embed_many") {
    return "openai";
  }
  return process.env.SOURCECADO_GENERATION_PROVIDER?.trim() || "deepseek";
}

export function resolveModel(input: ProviderResolutionInput): string {
  if (input.model?.trim()) {
    return input.model;
  }
  if (input.kind === "embed" || input.kind === "embed_many") {
    return process.env.SOURCECADO_EMBEDDING_MODEL || "text-embedding-3-small";
  }
  if (process.env.SOURCECADO_GENERATION_MODEL?.trim()) {
    return process.env.SOURCECADO_GENERATION_MODEL;
  }
  const providerName = resolveProviderName(input);
  if (providerName === "anthropic") return "claude-sonnet-4-6";
  if (providerName === "openai") {
    throw new ModelGatewayError(
      "config_error",
      'streamAgentTurn requires an explicit model when providerName is "openai" (no default model is assumed).',
    );
  }
  return "deepseek-chat";
}

export interface StreamAgentTurnInput {
  taskName: string;
  promptVersion: string;
  providerName?: string;
  model?: string;
  messages: LlmMessage[];
  tools: LlmToolDefinition[];
  trace: ModelGatewayTrace;
  maxTokens?: number;
  signal?: AbortSignal;
  adapter?: LlmAdapter;
}

export interface LlmTurnOutcome {
  message: LlmAssistantMessage;
  stopReason: StopReason;
  usage: LlmUsage;
  modelCallId: number;
}

function pickAdapter(providerName: string): LlmAdapter {
  if (providerName === "anthropic") return anthropicAdapter;
  if (providerName === "deepseek") return createOpenAiCompatAdapter("deepseek");
  if (providerName === "openai") return createOpenAiCompatAdapter("openai");
  throw new ModelGatewayError(
    "config_error",
    `Unsupported streaming provider: ${providerName}. Set SOURCECADO_GENERATION_PROVIDER to "anthropic" or "deepseek".`,
  );
}

export async function* streamAgentTurn(
  db: Sql,
  input: StreamAgentTurnInput,
): AsyncGenerator<LlmStreamEvent, LlmTurnOutcome, void> {
  const providerName = resolveProviderName({ kind: "stream_turn", providerName: input.providerName, model: input.model });
  const model = resolveModel({ kind: "stream_turn", providerName: input.providerName, model: input.model });
  const adapter = input.adapter ?? pickAdapter(providerName);

  const requestPayload = {
    messages: input.messages,
    toolNames: input.tools.map((t) => t.name),
    maxTokens: input.maxTokens ?? null,
  };
  const promptHash = createHash("sha256").update(JSON.stringify(requestPayload)).digest("hex");

  const runStep = await startRunStep(db, {
    runId: input.trace.runId,
    parentStepId: input.trace.parentStepId ?? null,
    stepKind: "model",
    name: input.taskName,
    input: requestPayload,
  });

  const blocks: LlmAssistantBlock[] = [];
  let pendingText = "";
  let usage: LlmUsage = { inputTokens: null, outputTokens: null, totalTokens: null };
  let stopReason: StopReason = "error";
  // Declared outside try so the catch handler can still fail the ledger
  // row if the INSERT itself is what throws (null means "never inserted").
  let modelCallId: number | null = null;
  // Guards the finally: a consumer that stops draining the generator
  // (early break / .return(), e.g. an SSE client disconnect cancelling the
  // stream) skips both the success path and the catch — without this, the
  // model_calls and run_steps rows would sit in 'running' forever.
  let outcomeRecorded = false;

  const flushPendingText = () => {
    if (pendingText) {
      blocks.push({ type: "text", text: pendingText });
      pendingText = "";
    }
  };

  try {
    const [modelCallRow] = await db`
      INSERT INTO model_calls (
        run_id, run_step_id, task_name, prompt_version, prompt_hash,
        provider, model, call_kind, status, request_json
      )
      VALUES (
        ${input.trace.runId}, ${runStep.id}, ${input.taskName}, ${input.promptVersion}, ${promptHash},
        ${providerName}, ${model}, 'stream_turn', 'running', ${toJson(db, requestPayload)}
      )
      RETURNING id
    `;
    modelCallId = Number(modelCallRow.id);

    const events = adapter({ model, messages: input.messages, tools: input.tools, maxTokens: input.maxTokens }, input.signal);
    for await (const event of events) {
      if (event.type === "text_delta") {
        pendingText += event.delta;
      } else if (event.type === "tool_call_start") {
        flushPendingText();
      } else if (event.type === "tool_call_end") {
        blocks.push({ type: "tool_use", id: event.id, name: event.name, input: event.input });
      } else if (event.type === "turn_end") {
        flushPendingText();
        stopReason = event.stopReason;
        usage = event.usage;
      }
      yield event;
    }

    const message: LlmAssistantMessage = { role: "assistant", content: blocks };
    const responsePayload = { message, stopReason };

    await db`
      UPDATE model_calls
      SET status = 'succeeded',
          response_json = ${toJson(db, responsePayload)},
          usage_json = ${toJson(db, usage)},
          input_tokens = ${usage.inputTokens},
          output_tokens = ${usage.outputTokens},
          total_tokens = ${usage.totalTokens},
          completed_at = now(),
          updated_at = now()
      WHERE id = ${modelCallId}
    `;
    await finishRunStep(db, { runStepId: runStep.id, output: responsePayload });
    outcomeRecorded = true;

    return { message, stopReason, usage, modelCallId };
  } catch (error) {
    const aborted = input.signal?.aborted === true || (error instanceof Error && error.name === "AbortError");
    const code = aborted ? "aborted" : "provider_error";
    const message = error instanceof Error ? error.message : String(error);

    if (modelCallId !== null) {
      await db`
        UPDATE model_calls
        SET status = 'failed', error_type = ${code}, error_message = ${message}, completed_at = now(), updated_at = now()
        WHERE id = ${modelCallId}
      `;
    }
    await failRunStep(db, { runStepId: runStep.id, errorType: code, errorMessage: message });
    outcomeRecorded = true;

    throw new ModelGatewayError(code, message, { cause: error });
  } finally {
    if (!outcomeRecorded) {
      const abandonMessage = "stream consumer abandoned the turn before completion";
      if (modelCallId !== null) {
        await db`
          UPDATE model_calls
          SET status = 'failed', error_type = 'abandoned', error_message = ${abandonMessage}, completed_at = now(), updated_at = now()
          WHERE id = ${modelCallId}
        `;
      }
      await failRunStep(db, { runStepId: runStep.id, errorType: "abandoned", errorMessage: abandonMessage });
    }
  }
}

function toProviderRequest(
  input: CallModelInput,
  providerName: string,
  model: string,
): ModelGatewayProviderRequest {
  return {
    kind: input.kind,
    prompt: "prompt" in input ? input.prompt : undefined,
    system: "system" in input ? input.system : undefined,
    value: "value" in input ? input.value : undefined,
    values: "values" in input ? input.values : undefined,
    schemaName: "schemaName" in input ? input.schemaName : undefined,
    taskName: input.taskName,
    promptVersion: input.promptVersion,
    providerName,
    model,
  };
}

function buildRequestPayload(input: CallModelInput): Record<string, unknown> {
  switch (input.kind) {
    case "generate_text":
      return { prompt: input.prompt, system: input.system ?? null };
    case "generate_object":
      return {
        prompt: input.prompt,
        system: input.system ?? null,
        schemaName: input.schemaName ?? null,
      };
    case "embed":
      return { value: input.value };
    case "embed_many":
      return { values: input.values };
  }
}

function buildResponsePayload(
  kind: CallModelInput["kind"],
  output: Partial<CallModelResult>,
  rawResponse: unknown,
): Record<string, unknown> {
  switch (kind) {
    case "generate_text":
      return { text: output.text, rawResponse: rawResponse ?? null };
    case "generate_object":
      return { object: output.object, rawResponse: rawResponse ?? null };
    case "embed":
      return { embedding: output.embedding, rawResponse: rawResponse ?? null };
    case "embed_many":
      return { embeddings: output.embeddings, rawResponse: rawResponse ?? null };
  }
}

function hashPromptPayload(input: CallModelInput): string {
  return createHash("sha256").update(JSON.stringify(buildRequestPayload(input))).digest("hex");
}

function toJson(db: Sql, value: unknown) {
  return db.json(value as postgres.JSONValue);
}

function normalizeUsage(usage: unknown): NormalizedUsage {
  if (!isObject(usage)) {
    return { raw: null, inputTokens: null, outputTokens: null, totalTokens: null };
  }
  if (typeof usage.tokens === "number") {
    return {
      raw: usage,
      inputTokens: usage.tokens,
      outputTokens: null,
      totalTokens: usage.tokens,
    };
  }

  const inputTokens = numberOrNull(usage.inputTokens);
  const outputTokens = numberOrNull(usage.outputTokens);
  const totalTokens =
    numberOrNull(usage.totalTokens) ??
    (inputTokens !== null || outputTokens !== null ? (inputTokens ?? 0) + (outputTokens ?? 0) : null);

  return {
    raw: usage,
    inputTokens,
    outputTokens,
    totalTokens,
  };
}

function getEmbeddingDimensions(kind: ModelCallKind, output: Partial<CallModelResult>): number | null {
  if (kind === "embed") {
    return output.embedding?.length ?? Number(process.env.SOURCECADO_EMBEDDING_DIMENSIONS || 1536);
  }
  if (kind === "embed_many") {
    return output.embeddings?.[0]?.length ?? Number(process.env.SOURCECADO_EMBEDDING_DIMENSIONS || 1536);
  }
  return null;
}

function requireEnv(name: string): void {
  if (!process.env[name]?.trim()) {
    throw new ModelGatewayError("missing_config", `${name} is required for Model Gateway provider calls.`);
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function serializeError(error: unknown): Record<string, unknown> {
  if (error instanceof Error) {
    return {
      name: error.name,
      message: error.message,
      stack: error.stack,
    };
  }
  return { message: String(error) };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
