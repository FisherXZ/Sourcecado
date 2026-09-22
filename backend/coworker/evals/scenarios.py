"""Deterministic fake-provider scenarios for the baseline harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from coworker.evals.environment import ConnectorFixtures
from coworker.provider import ModelUsage, ToolCall


@dataclass(frozen=True, slots=True)
class ProviderStep:
    text_deltas: tuple[str, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    usage: ModelUsage | None = None
    estimated_cost_usd: float | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AttachmentExpectation:
    """One durable person-file attachment: artifact, knowledge_gap, or source_ref."""

    record_type: str
    fields: tuple[tuple[str, Any], ...]


@dataclass(frozen=True, slots=True)
class PersonExpectation:
    apollo_id: str
    fields: tuple[tuple[str, Any], ...]
    attachments: tuple[AttachmentExpectation, ...] = ()
    events: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceExpectation:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class GmailDraftExpectation:
    to: str
    subject: str


@dataclass(frozen=True, slots=True)
class GmailSendExpectation:
    draft_id: str


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """The director's answer to one parked approval, keyed by tool-call id."""

    call_id: str
    decision: str

    def __post_init__(self) -> None:
        if self.decision not in {"allow", "deny", "cancel"}:
            raise ValueError(f"unknown approval decision {self.decision}")


@dataclass(frozen=True, slots=True)
class SeedAttachment:
    record_type: str
    fields: tuple[tuple[str, Any], ...]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class SeedEvent:
    source: str
    kind: str
    summary: str
    tool: str | None = None
    payload: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class SeedPerson:
    """Durable person-file state that already existed when the scene starts."""

    apollo_id: str
    first_name: str | None = None
    last_name_obfuscated: str | None = None
    title: str | None = None
    company: str | None = None
    target: str | None = None
    attachments: tuple[SeedAttachment, ...] = ()
    events: tuple[SeedEvent, ...] = ()
    sequence_state: str | None = None
    outcome: str | None = None
    bind_session: bool = False


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
    seed_people: tuple[SeedPerson, ...] = ()
    fixtures: ConnectorFixtures = field(default_factory=ConnectorFixtures)
    approvals: tuple[ApprovalDecision, ...] = ()
    expected_event_ledger: tuple[tuple[str, str], ...] | None = None
    expected_gmail_drafts: tuple[GmailDraftExpectation, ...] = ()
    expected_gmail_sends: tuple[GmailSendExpectation, ...] = ()
    expected_memories: tuple[str, ...] | None = None
    expected_catalog_violation: str | None = None


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
