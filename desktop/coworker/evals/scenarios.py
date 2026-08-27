"""Deterministic fake-provider scenarios for the baseline harness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from coworker.provider import ModelUsage, ToolCall


@dataclass(frozen=True, slots=True)
class ProviderStep:
    text_deltas: tuple[str, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    usage: ModelUsage | None = None
    estimated_cost_usd: float | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PersonExpectation:
    apollo_id: str
    fields: tuple[tuple[str, Any], ...]


@dataclass(frozen=True, slots=True)
class WorkspaceExpectation:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class EvalScenario:
    scenario_id: str
    prompt: str
    provider_steps: tuple[ProviderStep, ...]
    expected_tool_sequence: tuple[str, ...]
    expected_terminal_state: str
    expected_people: tuple[PersonExpectation, ...] = ()
    expected_workspace_files: tuple[WorkspaceExpectation, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    initial_messages: tuple[dict[str, Any], ...] = ()


def _usage(input_tokens: int, output_tokens: int) -> ModelUsage:
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cached_input_tokens=0,
        uncached_input_tokens=input_tokens,
        reasoning_tokens=0,
    )


def keep_person_scenario() -> EvalScenario:
    """Keep one curated candidate and prove the person file was persisted."""
    person = {
        "apolloId": "eval-alyssa",
        "firstName": "Alyssa",
        "lastNameObfuscated": "W***n",
        "title": "Partner",
        "organizationName": "Codeology",
    }
    return EvalScenario(
        scenario_id="keep-curated-person",
        prompt="Keep Alyssa for the Codeology dinner target.",
        provider_steps=(
            ProviderStep(
                tool_calls=(
                    ToolCall(
                        id="eval-keep-1",
                        name="people_keep",
                        arguments={
                            "people": [person],
                            "target": "Codeology dinner",
                        },
                    ),
                ),
                usage=_usage(12, 3),
                estimated_cost_usd=0.0000015,
            ),
            ProviderStep(
                text_deltas=("Kept Alyssa in a person file.",),
                usage=_usage(24, 5),
                estimated_cost_usd=0.0000029,
            ),
        ),
        expected_tool_sequence=("people_keep",),
        expected_terminal_state="complete",
        expected_people=(
            PersonExpectation(
                apollo_id="eval-alyssa",
                fields=(
                    ("first_name", "Alyssa"),
                    ("company", "Codeology"),
                    ("target", "Codeology dinner"),
                ),
            ),
        ),
        forbidden_tools=("apollo_enrich_contact", "gmail_send"),
    )


def baseline_scenarios() -> tuple[EvalScenario, ...]:
    return (keep_person_scenario(),)


def environment_probe_scenario() -> EvalScenario:
    return EvalScenario(
        scenario_id="credential-free-environment",
        prompt="Inspect the evaluation environment.",
        provider_steps=(
            ProviderStep(
                tool_calls=(
                    ToolCall(
                        id="eval-environment-1",
                        name="mcp__eval__environment_probe",
                        arguments={},
                    ),
                ),
                usage=_usage(4, 1),
            ),
            ProviderStep(text_deltas=("Environment inspected.",), usage=_usage(8, 2)),
        ),
        expected_tool_sequence=("mcp__eval__environment_probe",),
        expected_terminal_state="complete",
    )


def workspace_write_scenario() -> EvalScenario:
    return EvalScenario(
        scenario_id="workspace-write-confined",
        prompt="Write the evaluation note inside the granted workspace.",
        provider_steps=(
            ProviderStep(
                tool_calls=(
                    ToolCall(
                        id="eval-workspace-1",
                        name="fs_write",
                        arguments={
                            "grant_id": "$EVAL_WORKSPACE_GRANT",
                            "path": "notes/eval.txt",
                            "content": "isolated workspace effect",
                            "create_parents": True,
                        },
                    ),
                ),
                usage=_usage(6, 2),
            ),
            ProviderStep(text_deltas=("Wrote the isolated note.",), usage=_usage(10, 3)),
        ),
        expected_tool_sequence=("fs_write",),
        expected_terminal_state="complete",
        expected_workspace_files=(
            WorkspaceExpectation(
                path="notes/eval.txt", content="isolated workspace effect"
            ),
        ),
    )
