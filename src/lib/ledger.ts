import type postgres from "postgres";

export type RunStatus = "running" | "succeeded" | "failed" | "cancelled";
export type RunStepStatus = RunStatus | "skipped";
export type RunStepKind =
  | "agent"
  | "model"
  | "embedding"
  | "tool"
  | "retrieval"
  | "rerank"
  | "artifact"
  | "evaluation"
  | "system";

export interface RunRecord {
  id: number;
  runType: string;
  title: string | null;
  status: RunStatus;
  input: unknown;
  output: unknown;
  metadata: unknown;
  errorType: string | null;
  errorMessage: string | null;
  error: unknown;
  startedAt: Date;
  completedAt: Date | null;
  createdAt: Date;
  updatedAt: Date;
}

export interface RunStepRecord {
  id: number;
  runId: number;
  parentStepId: number | null;
  stepKind: RunStepKind;
  name: string;
  status: RunStepStatus;
  input: unknown;
  output: unknown;
  metadata: unknown;
  errorType: string | null;
  errorMessage: string | null;
  error: unknown;
  startedAt: Date;
  completedAt: Date | null;
  createdAt: Date;
  updatedAt: Date;
}

export interface ModelCallRecord {
  id: number;
  runId: number | null;
  runStepId: number | null;
  taskName: string;
  promptVersion: string;
  promptHash: string;
  provider: string;
  model: string;
  callKind: string;
  status: RunStatus;
  request: unknown;
  response: unknown;
  usage: unknown;
  inputTokens: number | null;
  outputTokens: number | null;
  totalTokens: number | null;
  embeddingDimensions: number | null;
  errorType: string | null;
  errorMessage: string | null;
  error: unknown;
  startedAt: Date;
  completedAt: Date | null;
  createdAt: Date;
  updatedAt: Date;
}

export interface ToolCallRecord {
  id: number;
  runId: number | null;
  runStepId: number | null;
  toolName: string;
  status: RunStatus;
  arguments: unknown;
  result: unknown;
  metadata: unknown;
  errorType: string | null;
  errorMessage: string | null;
  error: unknown;
  startedAt: Date;
  completedAt: Date | null;
  createdAt: Date;
  updatedAt: Date;
}

export interface RunStepTrace extends RunStepRecord {
  children: RunStepTrace[];
  modelCalls: ModelCallRecord[];
  toolCalls: ToolCallRecord[];
}

export interface RunTrace extends RunRecord {
  steps: RunStepTrace[];
}

export interface StartRunInput {
  runType: string;
  title?: string | null;
  input?: unknown;
  metadata?: unknown;
}

export interface FinishRunInput {
  runId: number;
  output?: unknown;
  metadata?: unknown;
}

export interface FailRunInput {
  runId: number;
  errorType: string;
  errorMessage: string;
  error?: unknown;
}

export interface StartRunStepInput {
  runId: number;
  parentStepId?: number | null;
  stepKind: RunStepKind;
  name: string;
  input?: unknown;
  metadata?: unknown;
}

export interface FinishRunStepInput {
  runStepId: number;
  output?: unknown;
  metadata?: unknown;
}

export interface FailRunStepInput {
  runStepId: number;
  errorType: string;
  errorMessage: string;
  error?: unknown;
}

export interface SkipRunStepInput {
  runStepId: number;
  output?: unknown;
}

export interface StartToolCallInput {
  runId: number;
  runStepId: number;
  toolName: string;
  arguments?: unknown;
  metadata?: unknown;
}

export interface FinishToolCallInput {
  toolCallId: number;
  result?: unknown;
  metadata?: unknown;
}

export interface FailToolCallInput {
  toolCallId: number;
  errorType: string;
  errorMessage: string;
  error?: unknown;
}

type Sql = postgres.Sql;
type Row = Record<string, unknown>;

export async function startRun(db: Sql, input: StartRunInput): Promise<RunRecord> {
  const inputJson = toJson(db, input.input);
  const metadataJson = toJson(db, input.metadata);
  const [row] = await db`
    INSERT INTO runs (run_type, title, status, input_json, metadata_json)
    VALUES (${input.runType}, ${input.title ?? null}, 'running', ${inputJson}, ${metadataJson})
    RETURNING *
  `;
  return mapRun(row);
}

export async function finishRun(db: Sql, input: FinishRunInput): Promise<RunRecord> {
  const outputJson = toJson(db, input.output);
  const metadataJson = toJson(db, input.metadata);
  const [row] = await db`
    UPDATE runs
    SET status = 'succeeded',
        output_json = ${outputJson},
        metadata_json = COALESCE(${metadataJson}, metadata_json),
        completed_at = now(),
        updated_at = now()
    WHERE id = ${input.runId}
    RETURNING *
  `;
  return mapRequiredRun(row, input.runId);
}

export async function failRun(db: Sql, input: FailRunInput): Promise<RunRecord> {
  const errorJson = toJson(db, input.error);
  const [row] = await db`
    UPDATE runs
    SET status = 'failed',
        error_type = ${input.errorType},
        error_message = ${input.errorMessage},
        error_json = ${errorJson},
        completed_at = now(),
        updated_at = now()
    WHERE id = ${input.runId}
    RETURNING *
  `;
  return mapRequiredRun(row, input.runId);
}

export async function startRunStep(db: Sql, input: StartRunStepInput): Promise<RunStepRecord> {
  await assertParentBelongsToRun(db, input.runId, input.parentStepId ?? null);

  const inputJson = toJson(db, input.input);
  const metadataJson = toJson(db, input.metadata);
  const [row] = await db`
    INSERT INTO run_steps (
      run_id,
      parent_step_id,
      step_kind,
      name,
      status,
      input_json,
      metadata_json
    )
    VALUES (
      ${input.runId},
      ${input.parentStepId ?? null},
      ${input.stepKind},
      ${input.name},
      'running',
      ${inputJson},
      ${metadataJson}
    )
    RETURNING *
  `;
  return mapRunStep(row);
}

export async function finishRunStep(db: Sql, input: FinishRunStepInput): Promise<RunStepRecord> {
  const outputJson = toJson(db, input.output);
  const metadataJson = toJson(db, input.metadata);
  const [row] = await db`
    UPDATE run_steps
    SET status = 'succeeded',
        output_json = ${outputJson},
        metadata_json = COALESCE(${metadataJson}, metadata_json),
        completed_at = now(),
        updated_at = now()
    WHERE id = ${input.runStepId}
    RETURNING *
  `;
  return mapRequiredRunStep(row, input.runStepId);
}

export async function failRunStep(db: Sql, input: FailRunStepInput): Promise<RunStepRecord> {
  const errorJson = toJson(db, input.error);
  const [row] = await db`
    UPDATE run_steps
    SET status = 'failed',
        error_type = ${input.errorType},
        error_message = ${input.errorMessage},
        error_json = ${errorJson},
        completed_at = now(),
        updated_at = now()
    WHERE id = ${input.runStepId}
    RETURNING *
  `;
  return mapRequiredRunStep(row, input.runStepId);
}

export async function skipRunStep(db: Sql, input: SkipRunStepInput): Promise<RunStepRecord> {
  const outputJson = toJson(db, input.output);
  const [row] = await db`
    UPDATE run_steps
    SET status = 'skipped',
        output_json = ${outputJson},
        completed_at = now(),
        updated_at = now()
    WHERE id = ${input.runStepId}
    RETURNING *
  `;
  return mapRequiredRunStep(row, input.runStepId);
}

export async function startToolCall(db: Sql, input: StartToolCallInput): Promise<ToolCallRecord> {
  await assertStepBelongsToRun(db, input.runId, input.runStepId);

  const argumentsJson = toJson(db, input.arguments);
  const metadataJson = toJson(db, input.metadata);
  const [row] = await db`
    INSERT INTO tool_calls (
      run_id,
      run_step_id,
      tool_name,
      status,
      arguments_json,
      metadata_json
    )
    VALUES (
      ${input.runId},
      ${input.runStepId},
      ${input.toolName},
      'running',
      ${argumentsJson},
      ${metadataJson}
    )
    RETURNING *
  `;
  return mapToolCall(row);
}

export async function finishToolCall(db: Sql, input: FinishToolCallInput): Promise<ToolCallRecord> {
  const resultJson = toJson(db, input.result);
  const metadataJson = toJson(db, input.metadata);
  const [row] = await db`
    UPDATE tool_calls
    SET status = 'succeeded',
        result_json = ${resultJson},
        metadata_json = COALESCE(${metadataJson}, metadata_json),
        completed_at = now(),
        updated_at = now()
    WHERE id = ${input.toolCallId}
    RETURNING *
  `;
  return mapRequiredToolCall(row, input.toolCallId);
}

export async function failToolCall(db: Sql, input: FailToolCallInput): Promise<ToolCallRecord> {
  const errorJson = toJson(db, input.error);
  const [row] = await db`
    UPDATE tool_calls
    SET status = 'failed',
        error_type = ${input.errorType},
        error_message = ${input.errorMessage},
        error_json = ${errorJson},
        completed_at = now(),
        updated_at = now()
    WHERE id = ${input.toolCallId}
    RETURNING *
  `;
  return mapRequiredToolCall(row, input.toolCallId);
}

export async function getRunTrace(db: Sql, runId: number): Promise<RunTrace | null> {
  const [runRow] = await db`
    SELECT * FROM runs WHERE id = ${runId}
  `;
  if (!runRow) {
    return null;
  }

  const stepRows = await db`
    SELECT * FROM run_steps WHERE run_id = ${runId} ORDER BY id
  `;
  const modelCallRows = await db`
    SELECT * FROM model_calls WHERE run_id = ${runId} ORDER BY id
  `;
  const toolCallRows = await db`
    SELECT * FROM tool_calls WHERE run_id = ${runId} ORDER BY id
  `;

  const stepsById = new Map<number, RunStepTrace>();
  const roots: RunStepTrace[] = [];
  for (const row of stepRows) {
    const step: RunStepTrace = {
      ...mapRunStep(row),
      children: [],
      modelCalls: [],
      toolCalls: [],
    };
    stepsById.set(step.id, step);
  }

  for (const step of stepsById.values()) {
    if (step.parentStepId && stepsById.has(step.parentStepId)) {
      stepsById.get(step.parentStepId)!.children.push(step);
    } else {
      roots.push(step);
    }
  }

  for (const row of modelCallRows) {
    const modelCall = mapModelCall(row);
    if (modelCall.runStepId && stepsById.has(modelCall.runStepId)) {
      stepsById.get(modelCall.runStepId)!.modelCalls.push(modelCall);
    }
  }

  for (const row of toolCallRows) {
    const toolCall = mapToolCall(row);
    if (toolCall.runStepId && stepsById.has(toolCall.runStepId)) {
      stepsById.get(toolCall.runStepId)!.toolCalls.push(toolCall);
    }
  }

  return {
    ...mapRun(runRow),
    steps: roots,
  };
}

async function assertParentBelongsToRun(
  db: Sql,
  runId: number,
  parentStepId: number | null,
): Promise<void> {
  if (!parentStepId) {
    return;
  }
  await assertStepBelongsToRun(db, runId, parentStepId, "parentStepId");
}

async function assertStepBelongsToRun(
  db: Sql,
  runId: number,
  runStepId: number,
  label = "runStepId",
): Promise<void> {
  const [row] = await db`
    SELECT run_id FROM run_steps WHERE id = ${runStepId}
  `;
  if (!row) {
    throw new Error(`${label} ${runStepId} does not exist.`);
  }
  if (Number(row.run_id) !== runId) {
    throw new Error(`${label} belongs to run ${row.run_id}, not run ${runId}.`);
  }
}

function toJson(db: Sql, value: unknown) {
  return value === undefined ? null : db.json(value as postgres.JSONValue);
}

function mapRequiredRun(row: Row | undefined, id: number): RunRecord {
  if (!row) {
    throw new Error(`Run ${id} does not exist.`);
  }
  return mapRun(row);
}

function mapRun(row: Row): RunRecord {
  return {
    id: Number(row.id),
    runType: String(row.run_type),
    title: nullableString(row.title),
    status: row.status as RunStatus,
    input: row.input_json ?? null,
    output: row.output_json ?? null,
    metadata: row.metadata_json ?? null,
    errorType: nullableString(row.error_type),
    errorMessage: nullableString(row.error_message),
    error: row.error_json ?? null,
    startedAt: row.started_at as Date,
    completedAt: nullableDate(row.completed_at),
    createdAt: row.created_at as Date,
    updatedAt: row.updated_at as Date,
  };
}

function mapRequiredRunStep(row: Row | undefined, id: number): RunStepRecord {
  if (!row) {
    throw new Error(`Run step ${id} does not exist.`);
  }
  return mapRunStep(row);
}

function mapRunStep(row: Row): RunStepRecord {
  return {
    id: Number(row.id),
    runId: Number(row.run_id),
    parentStepId: nullableNumber(row.parent_step_id),
    stepKind: row.step_kind as RunStepKind,
    name: String(row.name),
    status: row.status as RunStepStatus,
    input: row.input_json ?? null,
    output: row.output_json ?? null,
    metadata: row.metadata_json ?? null,
    errorType: nullableString(row.error_type),
    errorMessage: nullableString(row.error_message),
    error: row.error_json ?? null,
    startedAt: row.started_at as Date,
    completedAt: nullableDate(row.completed_at),
    createdAt: row.created_at as Date,
    updatedAt: row.updated_at as Date,
  };
}

function mapModelCall(row: Row): ModelCallRecord {
  return {
    id: Number(row.id),
    runId: nullableNumber(row.run_id),
    runStepId: nullableNumber(row.run_step_id),
    taskName: String(row.task_name),
    promptVersion: String(row.prompt_version),
    promptHash: String(row.prompt_hash),
    provider: String(row.provider),
    model: String(row.model),
    callKind: String(row.call_kind),
    status: row.status as RunStatus,
    request: row.request_json ?? null,
    response: row.response_json ?? null,
    usage: row.usage_json ?? null,
    inputTokens: nullableNumber(row.input_tokens),
    outputTokens: nullableNumber(row.output_tokens),
    totalTokens: nullableNumber(row.total_tokens),
    embeddingDimensions: nullableNumber(row.embedding_dimensions),
    errorType: nullableString(row.error_type),
    errorMessage: nullableString(row.error_message),
    error: row.error_json ?? null,
    startedAt: row.started_at as Date,
    completedAt: nullableDate(row.completed_at),
    createdAt: row.created_at as Date,
    updatedAt: row.updated_at as Date,
  };
}

function mapRequiredToolCall(row: Row | undefined, id: number): ToolCallRecord {
  if (!row) {
    throw new Error(`Tool call ${id} does not exist.`);
  }
  return mapToolCall(row);
}

function mapToolCall(row: Row): ToolCallRecord {
  return {
    id: Number(row.id),
    runId: nullableNumber(row.run_id),
    runStepId: nullableNumber(row.run_step_id),
    toolName: String(row.tool_name),
    status: row.status as RunStatus,
    arguments: row.arguments_json ?? null,
    result: row.result_json ?? null,
    metadata: row.metadata_json ?? null,
    errorType: nullableString(row.error_type),
    errorMessage: nullableString(row.error_message),
    error: row.error_json ?? null,
    startedAt: row.started_at as Date,
    completedAt: nullableDate(row.completed_at),
    createdAt: row.created_at as Date,
    updatedAt: row.updated_at as Date,
  };
}

function nullableNumber(value: unknown): number | null {
  return value === null || value === undefined ? null : Number(value);
}

function nullableString(value: unknown): string | null {
  return value === null || value === undefined ? null : String(value);
}

function nullableDate(value: unknown): Date | null {
  return value === null || value === undefined ? null : (value as Date);
}
