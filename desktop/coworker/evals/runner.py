"""Execution engine for isolated deterministic and explicitly live evaluations."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import queue
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, AsyncIterator

from coworker.events import TurnIdentity
from coworker.evals.environment import (
    EvalEnvironment,
    sensitive_environment_keys,
    write_private_text,
)
from coworker.evals.models import (
    EvalRunResult,
    EvalVariant,
    InvariantResult,
    JudgeObservation,
    RunArtifacts,
    RunBudget,
    RunMeasurements,
)
from coworker.evals.scenarios import EvalScenario, ProviderStep
from coworker.evals.transcript import compact_transcript, transcript_issues
from coworker.provider import (
    ProviderErrorKind,
    ProviderStreamError,
    ProviderTerminal,
    StreamChunk,
    StreamKind,
)
from coworker.telemetry import (
    CompactionEvent,
    CompactionReason,
    InMemoryTelemetryAdapter,
    TelemetryRecorder,
    ToolSpan,
    TraceContext,
    record_to_dict,
)
from coworker.tools import OPENAI_TOOLS
from coworker.turn import run_turn

ARTIFACT_WARNING = (
    "Evaluation artifacts may contain prompts, responses, tool arguments, and tool "
    "output. Keep them local, review before sharing, and never commit them."
)
_ENVIRONMENT_PROBE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "mcp__eval__environment_probe",
        "description": "Report sensitive environment variable names in an eval child.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}


class EvalBudgetExceeded(RuntimeError):
    pass


class EvalToolCatalogViolation(RuntimeError):
    pass


class LiveRunNotAuthorized(RuntimeError):
    pass


class BudgetedProvider:
    """Apply the eval run budget to any injected live provider."""

    def __init__(self, provider: Any, budget: RunBudget) -> None:
        self.provider = provider
        self.provider_id = str(getattr(provider, "provider_id", "unknown"))
        self.model_id = str(getattr(provider, "model_id", "unknown"))
        self.uses_transient_context = bool(
            getattr(provider, "uses_transient_context", False)
        )
        self._budget = budget
        self._provider_calls = 0
        self._total_tokens = 0
        self._budget_error: str | None = None
        self._tool_catalog_error: str | None = None
        self.environment_samples: list[list[str]] = []
        self.transcript_issues: list[str] = []

    @property
    def budget_error(self) -> str | None:
        return self._budget_error or getattr(self.provider, "budget_error", None)

    @property
    def tool_catalog_error(self) -> str | None:
        return self._tool_catalog_error

    def bind_workspace(self, grant_id: str) -> None:
        bind = getattr(self.provider, "bind_workspace", None)
        if callable(bind):
            bind(grant_id)

    async def astream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        context_id: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self.environment_samples.append(sensitive_environment_keys())
        self._provider_calls += 1
        self.transcript_issues.extend(
            f"provider call {self._provider_calls}: {issue}"
            for issue in transcript_issues(messages)
        )
        if self._provider_calls > self._budget.max_provider_calls:
            self._budget_error = (
                f"provider calls {self._provider_calls} exceed budget "
                f"{self._budget.max_provider_calls}"
            )
            raise EvalBudgetExceeded(self._budget_error)
        stream_kwargs: dict[str, Any] = {"messages": messages, "tools": tools}
        active_tools = {
            str((schema.get("function") or {}).get("name") or "")
            for schema in tools or []
            if isinstance(schema, dict)
        }
        if self.uses_transient_context:
            stream_kwargs["context_id"] = context_id
        latest_total: int | None = None
        try:
            async for chunk in self.provider.astream(**stream_kwargs):
                usage = chunk.usage
                if chunk.terminal is not None and chunk.terminal.usage is not None:
                    usage = chunk.terminal.usage
                if usage is not None:
                    latest_total = usage.total_tokens
                    maximum = self._budget.max_total_tokens
                    if (
                        maximum is not None
                        and self._total_tokens + latest_total > maximum
                    ):
                        self._budget_error = (
                            f"total tokens {self._total_tokens + latest_total} exceed "
                            f"budget {maximum}"
                        )
                        raise EvalBudgetExceeded(self._budget_error)
                unauthorized = sorted(
                    {
                        call.name
                        for call in chunk.tool_calls or []
                        if call.name not in active_tools
                    }
                )
                if unauthorized:
                    self._tool_catalog_error = (
                        "provider called tools outside the active catalog: "
                        + ", ".join(unauthorized)
                    )
                    raise EvalToolCatalogViolation(self._tool_catalog_error)
                yield chunk
        except (EvalBudgetExceeded, EvalToolCatalogViolation, ProviderStreamError):
            raise
        except Exception as exc:
            raise ProviderStreamError(
                provider=self.provider_id,
                model=self.model_id,
                kind=ProviderErrorKind.PROVIDER,
                message="provider request failed",
                retryable=False,
            ) from exc
        if latest_total is not None:
            self._total_tokens += latest_total


class ScenarioProvider:
    """Deterministic provider whose transcript is fixed by an eval scenario."""

    def __init__(self, *, variant: EvalVariant, steps: tuple[ProviderStep, ...]) -> None:
        self.provider_id = variant.provider
        self.model_id = variant.model
        self.steps = steps
        self.calls: list[dict[str, Any]] = []
        self._index = 0
        self._total_tokens = 0
        self._budget = variant.run_budget
        self.budget_error: str | None = None
        self.workspace_grant_id: str | None = None
        self.environment_samples: list[list[str]] = []

    def bind_workspace(self, grant_id: str) -> None:
        self.workspace_grant_id = grant_id

    def _arguments(self, value: Any) -> Any:
        if value == "$EVAL_WORKSPACE_GRANT":
            if self.workspace_grant_id is None:
                raise RuntimeError("eval workspace grant is not bound")
            return self.workspace_grant_id
        if isinstance(value, dict):
            return {key: self._arguments(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._arguments(item) for item in value]
        return value

    async def astream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        context_id: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        del context_id
        self.environment_samples.append(sensitive_environment_keys())
        call_number = len(self.calls) + 1
        if call_number > self._budget.max_provider_calls:
            self.budget_error = (
                f"provider calls {call_number} exceed budget "
                f"{self._budget.max_provider_calls}"
            )
            raise EvalBudgetExceeded(self.budget_error)
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": [dict(tool) for tool in tools or ()],
            }
        )
        if self._index >= len(self.steps):
            self.budget_error = "scenario provider exhausted its scripted steps"
            raise EvalBudgetExceeded(self.budget_error)
        step = self.steps[self._index]
        self._index += 1
        if step.usage is not None:
            next_total = self._total_tokens + step.usage.total_tokens
            maximum = self._budget.max_total_tokens
            if maximum is not None and next_total > maximum:
                self.budget_error = f"total tokens {next_total} exceed budget {maximum}"
                raise EvalBudgetExceeded(self.budget_error)
            self._total_tokens = next_total
        yield StreamChunk.started(provider=self.provider_id, model=self.model_id)
        if step.error is not None:
            raise RuntimeError(step.error)
        for delta in step.text_deltas:
            yield StreamChunk(text_delta=delta)
        if step.usage is not None:
            yield StreamChunk(usage=step.usage)
        finish_reason = "tool_calls" if step.tool_calls else "stop"
        yield StreamChunk(
            kind=StreamKind.TERMINAL,
            finish_reason=finish_reason,
            terminal=ProviderTerminal(
                stop_reason=finish_reason,
                usage=step.usage,
                latency_ms=0.0,
                estimated_cost_usd=step.estimated_cost_usd,
            ),
        )
        if step.tool_calls:
            yield StreamChunk(
                tool_calls=[
                    type(call)(
                        id=call.id,
                        name=call.name,
                        arguments=self._arguments(call.arguments),
                    )
                    for call in step.tool_calls
                ]
            )


def _selected_tools(names: tuple[str, ...]) -> list[dict[str, Any]]:
    by_name = {schema["function"]["name"]: schema for schema in OPENAI_TOOLS}
    by_name["mcp__eval__environment_probe"] = _ENVIRONMENT_PROBE_SCHEMA
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise ValueError(f"unknown eval tool: {unknown[0]}")
    return [by_name[name] for name in names]


def _prompt(variant: EvalVariant):
    def render(*_args: Any, **_kwargs: Any) -> str:
        return variant.system_prompt

    return render


def _terminal_state(events: list[dict[str, Any]], run_result: dict[str, Any]) -> str:
    terminal = next(
        (
            event
            for event in reversed(events)
            if event.get("type") in {"turn_end", "turn_stopped", "error"}
        ),
        None,
    )
    if terminal is not None:
        return str(terminal.get("state") or "unknown")
    return str(run_result.get("status") or "unknown")


def _spawned_run(
    output: Any,
    artifact_root: str,
    scenario: EvalScenario,
    variant: EvalVariant,
    repetition: int,
    provider: Any,
    execution_mode: str,
    nondeterministic: bool,
    judge: JudgeObservation | None,
) -> None:
    try:
        result = EvalRunner(Path(artifact_root))._run_local(
            scenario,
            variant,
            repetition=repetition,
            provider=provider,
            execution_mode=execution_mode,
            nondeterministic=nondeterministic,
            judge=judge,
            process_isolated=True,
        )
        output.put(("ok", result))
    except BaseException as exc:
        output.put(
            (
                "error",
                f"{type(exc).__name__}: eval child failed",
            )
        )


class EvalRunner:
    def __init__(self, artifact_root: str | Path) -> None:
        self.artifact_root = Path(artifact_root)

    def run_fake(
        self,
        scenario: EvalScenario,
        variant: EvalVariant,
        *,
        repetition: int,
        judge: JudgeObservation | None = None,
    ) -> EvalRunResult:
        provider = ScenarioProvider(variant=variant, steps=scenario.provider_steps)
        return self._run(
            scenario,
            variant,
            repetition=repetition,
            provider=provider,
            execution_mode="fake",
            nondeterministic=False,
            judge=judge,
        )

    def run_live(
        self,
        scenario: EvalScenario,
        variant: EvalVariant,
        *,
        provider: Any,
        repetition: int,
        opt_in: bool,
        judge: JudgeObservation | None = None,
    ) -> EvalRunResult:
        if not opt_in:
            raise LiveRunNotAuthorized(
                "live evaluations require explicit opt-in for every invocation"
            )
        if provider is None:
            raise ValueError("live provider is unavailable")
        return self._run(
            scenario,
            variant,
            repetition=repetition,
            provider=BudgetedProvider(provider, variant.run_budget),
            execution_mode="live",
            nondeterministic=True,
            judge=judge,
        )

    def _run(
        self,
        scenario: EvalScenario,
        variant: EvalVariant,
        *,
        repetition: int,
        provider: Any,
        execution_mode: str,
        nondeterministic: bool,
        judge: JudgeObservation | None,
    ) -> EvalRunResult:
        _selected_tools(variant.tool_catalog)
        context = multiprocessing.get_context("spawn")
        output = context.Queue()
        process = context.Process(
            target=_spawned_run,
            args=(
                output,
                str(self.artifact_root),
                scenario,
                variant,
                repetition,
                provider,
                execution_mode,
                nondeterministic,
                judge,
            ),
        )
        process.start()
        try:
            status, payload = output.get(timeout=120)
        except queue.Empty as exc:
            process.terminate()
            process.join(timeout=5)
            output.close()
            output.join_thread()
            raise RuntimeError("eval child did not report within 120 seconds") from exc
        process.join(timeout=10)
        output.close()
        output.join_thread()
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            raise RuntimeError("eval child did not exit after reporting")
        if status != "ok":
            raise RuntimeError(str(payload))
        if process.exitcode != 0:
            raise RuntimeError(f"eval child exited with status {process.exitcode}")
        return payload

    def _run_local(
        self,
        scenario: EvalScenario,
        variant: EvalVariant,
        *,
        repetition: int,
        provider: Any,
        execution_mode: str,
        nondeterministic: bool,
        judge: JudgeObservation | None,
        process_isolated: bool,
    ) -> EvalRunResult:
        if repetition < 1:
            raise ValueError("repetition must be positive")
        tools = _selected_tools(variant.tool_catalog)
        if not isinstance(provider, BudgetedProvider):
            provider = BudgetedProvider(provider, variant.run_budget)
        label = f"{scenario.scenario_id}-{variant.name}-r{repetition}"
        environment = EvalEnvironment.create(
            self.artifact_root,
            label=label,
            apply_environment=process_isolated,
        )
        session_id = f"eval-{uuid.uuid4().hex}"
        run_id = f"run-{uuid.uuid4().hex}"
        identity = TurnIdentity(
            session_id=session_id,
            run_id=run_id,
            message_id=f"message-{uuid.uuid4().hex}",
            part_id=f"part-{uuid.uuid4().hex}",
        )
        environment.store.create_session(session_id)
        bind_workspace = getattr(provider, "bind_workspace", None)
        if callable(bind_workspace):
            bind_workspace(str(environment.workspace_grant["id"]))
        for message in scenario.initial_messages:
            environment.store.append(session_id, dict(message))
        adapter = InMemoryTelemetryAdapter()
        recorder = TelemetryRecorder(adapter)
        self._compact_if_needed(
            environment=environment,
            session_id=session_id,
            run_id=run_id,
            variant=variant,
            recorder=recorder,
        )
        emitted: list[dict[str, Any]] = []

        async def emit(event: dict[str, Any]) -> None:
            emitted.append(event)

        infrastructure_error: str | None = None
        try:
            raw_result = asyncio.run(
                run_turn(
                    text=scenario.prompt,
                    sid=session_id,
                    store=environment.store,
                    provider=provider,
                    persona=None,
                    skills=None,
                    inbox=environment.inbox,
                    openai_tools=tools,
                    execute_kwargs={
                        "store": environment.store,
                        "gmail": environment.connectors.gmail,
                        "drive": environment.connectors.drive,
                        "calendar": environment.connectors.calendar,
                        "http": environment.connectors.http,
                        "apollo_key": None,
                        "tavily_key": None,
                        "mcp": environment.connectors.mcp,
                        "people": environment.people,
                        "workspace_runtime": environment.workspace_runtime,
                        "actor": "assistant",
                    },
                    emit=emit,
                    wait_permission=None,
                    system_prompt_fn=_prompt(variant),
                    identity=identity,
                    telemetry=recorder,
                )
            )
        except Exception as exc:  # run_turn normally contains failures itself
            raw_result = {"status": "error", "text": ""}
            infrastructure_error = f"{type(exc).__name__}: {exc}"
        messages = environment.store.load(session_id)
        events = environment.store.load_events(session_id)
        terminal_state = _terminal_state(events, raw_result)
        budget_error = getattr(provider, "budget_error", None)
        tool_catalog_error = getattr(provider, "tool_catalog_error", None)
        if (
            raw_result.get("status") == "error"
            and not budget_error
            and not tool_catalog_error
            and infrastructure_error is None
        ):
            infrastructure_error = "agent run ended with infrastructure error"
        tool_sequence = tuple(
            str(event["name"])
            for event in events
            if event.get("type") == "tool_started"
        )
        effects, effect_checks = self._persisted_effects(environment, scenario)
        invariants = [
            InvariantResult(
                name="tool_sequence",
                passed=tool_sequence == scenario.expected_tool_sequence,
                detail=(
                    f"expected {scenario.expected_tool_sequence!r}; got {tool_sequence!r}"
                ),
            ),
            InvariantResult(
                name="terminal_state",
                passed=terminal_state == scenario.expected_terminal_state,
                detail=(
                    f"expected {scenario.expected_terminal_state!r}; got "
                    f"{terminal_state!r}"
                ),
            ),
            InvariantResult(
                name="forbidden_tools",
                passed=not set(tool_sequence).intersection(scenario.forbidden_tools),
                detail=f"forbidden={scenario.forbidden_tools!r}; got={tool_sequence!r}",
            ),
            *effect_checks,
        ]
        if budget_error:
            invariants.append(
                InvariantResult(
                    name="run_budget",
                    passed=False,
                    detail=str(budget_error),
                )
            )
        invariants.append(
            InvariantResult(
                name="tool_catalog",
                passed=not tool_catalog_error,
                detail=(
                    "all provider tool calls were in the active catalog"
                    if not tool_catalog_error
                    else str(tool_catalog_error)
                ),
            )
        )
        telemetry = tuple(record_to_dict(record) for record in adapter.records)
        context_ok = bool(telemetry) and all(
            record["context"] == {"session_id": session_id, "run_id": run_id}
            for record in telemetry
        )
        invariants.append(
            InvariantResult(
                name="telemetry_parentage",
                passed=context_ok,
                detail="all closed telemetry records must match the native session/run",
            )
        )
        provider_sensitive_keys = sorted(
            {
                key
                for sample in getattr(provider, "environment_samples", ())
                for key in sample
            }
        )
        tool_sensitive_keys = sorted(
            {
                str(key)
                for event in events
                if event.get("type") == "tool_finished"
                and event.get("name") == "mcp__eval__environment_probe"
                for key in (event.get("result") or {}).get("sensitive_keys", [])
            }
        )
        invariants.append(
            InvariantResult(
                name="credential_environment",
                passed=not provider_sensitive_keys and not tool_sensitive_keys,
                detail=(
                    f"provider keys={provider_sensitive_keys!r}; "
                    f"tool keys={tool_sensitive_keys!r}"
                ),
            )
        )
        provider_transcript_issues = list(
            getattr(provider, "transcript_issues", ())
        )
        invariants.append(
            InvariantResult(
                name="transcript_integrity",
                passed=not provider_transcript_issues,
                detail=(
                    "valid assistant/tool transcript"
                    if not provider_transcript_issues
                    else "; ".join(provider_transcript_issues)
                ),
            )
        )
        environment_exact = dict(os.environ) == environment.credential_environment
        invariants.append(
            InvariantResult(
                name="minimal_environment",
                passed=environment_exact,
                detail=(
                    f"expected names={sorted(environment.credential_environment)!r}; "
                    f"got names={sorted(os.environ)!r}"
                ),
            )
        )
        metrics = adapter.current_run_metrics(run_id=run_id, now_ns=time.monotonic_ns())
        measurements = RunMeasurements(
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
            total_tokens=metrics.total_tokens,
            latency_ms=metrics.elapsed_ms,
            estimated_cost_usd=metrics.estimated_cost_usd,
            retry_count=metrics.retry_count,
            compaction_count=metrics.compaction_count,
        )
        conversation_path = environment.store.conv_dir / f"{session_id}.jsonl"
        events_path = environment.store.event_dir / f"{session_id}.jsonl"
        telemetry_path = environment.root / "telemetry.jsonl"
        result_path = environment.root / "result.json"
        write_private_text(
            telemetry_path,
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in telemetry),
        )
        artifacts = RunArtifacts(
            root=str(environment.root.resolve()),
            state_dir=str(environment.state_dir.resolve()),
            workspace_dir=str(environment.workspace_dir.resolve()),
            conversation_jsonl=str(conversation_path.resolve()),
            events_jsonl=str(events_path.resolve()),
            telemetry_jsonl=str(telemetry_path.resolve()),
            result_json=str(result_path.resolve()),
        )
        result = EvalRunResult(
            scenario_id=scenario.scenario_id,
            variant_name=variant.name,
            repetition=repetition,
            pair_key=f"{scenario.scenario_id}:r{repetition}",
            execution_mode=execution_mode,
            provider=str(getattr(provider, "provider_id", variant.provider)),
            model=str(getattr(provider, "model_id", variant.model)),
            prompt_version=variant.prompt_version,
            nondeterministic=nondeterministic,
            session_id=session_id,
            run_id=run_id,
            terminal_state=terminal_state,
            tool_sequence=tool_sequence,
            provider_calls=sum(
                record.get("record_type") == "span_started"
                and (record.get("span") or {}).get("span_type") == "provider"
                for record in telemetry
            ),
            invariants=tuple(invariants),
            measurements=measurements,
            variant=asdict(variant),
            session_artifact={
                "session": environment.store.index(session_id),
                "messages": messages,
                "events": events,
            },
            telemetry=telemetry,
            persisted_effects=effects,
            execution_environment={
                "applied": dict(environment.credential_environment),
                "provider_sensitive_keys": provider_sensitive_keys,
                "tool_sensitive_keys": tool_sensitive_keys,
                "process_isolated": process_isolated,
                "child_pid": os.getpid(),
                "workspace_grant": dict(environment.workspace_grant),
            },
            artifacts=artifacts,
            infrastructure_error=infrastructure_error,
            judge=judge,
        )
        write_private_text(
            result_path,
            json.dumps(
                {"warning": ARTIFACT_WARNING, "result": asdict(result)},
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
        )
        environment.close()
        return result

    @staticmethod
    def _compact_if_needed(
        *,
        environment: EvalEnvironment,
        session_id: str,
        run_id: str,
        variant: EvalVariant,
        recorder: TelemetryRecorder,
    ) -> None:
        policy = variant.compaction
        messages = environment.store.load(session_id)
        if not policy.enabled or len(messages) < policy.threshold_messages:
            return
        compacted = compact_transcript(
            messages, retain_messages=policy.retain_messages
        )
        environment.store.replace_all(session_id, compacted)
        span = recorder.start_span(
            ToolSpan(tool_name="eval", operation="history.compact"),
            TraceContext(session_id=session_id, run_id=run_id),
        )
        span.record(
            CompactionEvent(
                operation="history.compact",
                reason=CompactionReason.POLICY,
                input_tokens=None,
                output_tokens=None,
            )
        )
        span.finish()

    @staticmethod
    def _persisted_effects(
        environment: EvalEnvironment,
        scenario: EvalScenario,
    ) -> tuple[dict[str, Any], list[InvariantResult]]:
        with environment.people._lock:
            person_ids = [
                str(row["person_id"])
                for row in environment.people._conn.execute(
                    """
                    SELECT person_id FROM people
                    WHERE deleted_at IS NULL
                    ORDER BY person_id
                    """
                ).fetchall()
            ]
        people = [
            person
            for person_id in person_ids
            if (person := environment.people.get(person_id)) is not None
        ]
        people_by_apollo = {
            str(person.get("apollo_id") or person["person_id"]): person
            for person in people
        }
        checks: list[InvariantResult] = []
        for expectation in scenario.expected_people:
            person = people_by_apollo.get(expectation.apollo_id)
            matches = person is not None and all(
                person.get(field_name) == expected
                for field_name, expected in expectation.fields
            )
            checks.append(
                InvariantResult(
                    name=f"person:{expectation.apollo_id}",
                    passed=matches,
                    detail=(
                        f"expected fields {dict(expectation.fields)!r}; "
                        f"got {person!r}"
                    ),
                )
            )
        expected_people = {item.apollo_id for item in scenario.expected_people}
        actual_people = set(people_by_apollo)
        checks.append(
            InvariantResult(
                name="people_effect_set",
                passed=actual_people == expected_people,
                detail=(
                    f"expected people={sorted(expected_people)!r}; "
                    f"actual people={sorted(actual_people)!r}"
                ),
            )
        )
        workspace_files: list[dict[str, Any]] = []
        workspace_directories: list[str] = []
        for path in sorted(environment.workspace_dir.rglob("*")):
            relative = path.relative_to(environment.workspace_dir).as_posix()
            if path.is_symlink():
                workspace_files.append({"path": relative, "kind": "symlink"})
                continue
            if path.is_dir():
                workspace_directories.append(relative)
                continue
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                workspace_files.append({"path": relative, "kind": "binary"})
            else:
                workspace_files.append({"path": relative, "content": content})
        workspace_by_path = {str(item["path"]): item for item in workspace_files}
        for expectation in scenario.expected_workspace_files:
            content = workspace_by_path.get(expectation.path, {}).get("content")
            checks.append(
                InvariantResult(
                    name=f"workspace:{expectation.path}",
                    passed=content == expectation.content,
                    detail=(
                        f"expected {expectation.content!r}; got {content!r} at "
                        f"{expectation.path!r}"
                    ),
                )
            )
        expected_workspace = {
            item.path for item in scenario.expected_workspace_files
        }
        actual_workspace = set(workspace_by_path)
        expected_directories = {
            parent.as_posix()
            for item in scenario.expected_workspace_files
            for parent in Path(item.path).parents
            if parent.as_posix() != "."
        }
        actual_directories = set(workspace_directories)
        checks.append(
            InvariantResult(
                name="workspace_effect_set",
                passed=(
                    actual_workspace == expected_workspace
                    and actual_directories == expected_directories
                ),
                detail=(
                    f"expected files={sorted(expected_workspace)!r}; "
                    f"actual files={sorted(actual_workspace)!r}; "
                    f"expected directories={sorted(expected_directories)!r}; "
                    f"actual directories={sorted(actual_directories)!r}"
                ),
            )
        )
        grant = environment.workspace_runtime.grants.require(
            str(environment.workspace_grant["id"])
        )
        grant_confined = (
            Path(str(grant["path"])).resolve()
            == environment.workspace_dir.resolve()
            and grant.get("access") == "read_write"
            and grant.get("allow_shell") is False
        )
        checks.append(
            InvariantResult(
                name="workspace_confinement",
                passed=grant_confined,
                detail=f"run-local grant={grant!r}",
            )
        )
        return {
            "people": people,
            "workspace_files": workspace_files,
            "workspace_directories": workspace_directories,
        }, checks
