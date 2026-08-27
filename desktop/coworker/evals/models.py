"""Typed contracts for reproducible Sourcecado evaluation variants."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


def _required(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class CompactionPolicy:
    enabled: bool = False
    threshold_messages: int = 24
    retain_messages: int = 12

    def __post_init__(self) -> None:
        if self.threshold_messages < 1:
            raise ValueError("threshold_messages must be positive")
        if self.retain_messages < 1:
            raise ValueError("retain_messages must be positive")
        if self.retain_messages > self.threshold_messages:
            raise ValueError("retain_messages cannot exceed threshold_messages")


@dataclass(frozen=True, slots=True)
class RunBudget:
    max_provider_calls: int = 8
    max_total_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.max_provider_calls < 1:
            raise ValueError("max_provider_calls must be positive")
        if self.max_total_tokens is not None and self.max_total_tokens < 1:
            raise ValueError("max_total_tokens must be positive or null")


@dataclass(frozen=True, slots=True)
class EvalVariant:
    name: str
    prompt_version: str
    system_prompt: str
    tool_catalog: tuple[str, ...]
    provider: str
    model: str
    compaction: CompactionPolicy = field(default_factory=CompactionPolicy)
    run_budget: RunBudget = field(default_factory=RunBudget)

    def __post_init__(self) -> None:
        for field_name in ("name", "prompt_version", "provider", "model"):
            _required(getattr(self, field_name), field_name)
        if not isinstance(self.system_prompt, str):
            raise ValueError("system_prompt must be a string")
        if not isinstance(self.tool_catalog, tuple) or any(
            not isinstance(name, str) or not name.strip()
            for name in self.tool_catalog
        ):
            raise ValueError("tool_catalog must contain non-empty tool names")
        if len(set(self.tool_catalog)) != len(self.tool_catalog):
            raise ValueError("tool_catalog must not contain duplicates")


@dataclass(frozen=True, slots=True)
class InvariantResult:
    name: str
    passed: bool
    detail: str
    kind: str = "deterministic"


@dataclass(frozen=True, slots=True)
class RunMeasurements:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    latency_ms: float | None
    estimated_cost_usd: float | None
    retry_count: int
    compaction_count: int


@dataclass(frozen=True, slots=True)
class RunArtifacts:
    root: str
    state_dir: str
    workspace_dir: str
    conversation_jsonl: str
    events_jsonl: str
    telemetry_jsonl: str
    result_json: str


@dataclass(frozen=True, slots=True)
class JudgeObservation:
    judge: str
    score: float
    rationale: str = ""

    def __post_init__(self) -> None:
        _required(self.judge, "judge")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise ValueError("score must be numeric")
        if not math.isfinite(self.score) or not 0 <= self.score <= 1:
            raise ValueError("score must be finite and between 0 and 1")


@dataclass(frozen=True, slots=True)
class EvalRunResult:
    scenario_id: str
    variant_name: str
    repetition: int
    pair_key: str
    execution_mode: str
    provider: str
    model: str
    prompt_version: str
    nondeterministic: bool
    session_id: str
    run_id: str
    terminal_state: str
    tool_sequence: tuple[str, ...]
    provider_calls: int
    invariants: tuple[InvariantResult, ...]
    measurements: RunMeasurements
    variant: dict[str, Any]
    session_artifact: dict[str, Any]
    telemetry: tuple[dict[str, Any], ...]
    persisted_effects: dict[str, Any]
    execution_environment: dict[str, Any]
    artifacts: RunArtifacts
    infrastructure_error: str | None = None
    judge: JudgeObservation | None = None

    @property
    def passed(self) -> bool:
        return self.infrastructure_error is None and all(
            item.passed for item in self.invariants
        )
