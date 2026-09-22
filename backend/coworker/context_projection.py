"""The approved contract for bounded Sourcecado context projection.

The active prompt runtime imports this module. `brief.py` builds the person-file
section from it (#70), and `server.py` builds the saved-memory section from it
under the `context-projection-v1` category rules Fisher approved on issue #58.
A change to selection, bounding, or scope-mismatch behaviour here changes what
the model sees on the next request.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256


class ContextCategory(StrEnum):
    OPERATOR_PREFERENCE = "operator_preference"
    SEQUENCE_STATE = "sequence_state"
    PERSON_EVIDENCE = "person_evidence"
    SESSION_WORKING = "session_working"


class ContextState(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    CONFLICTING = "conflicting"
    MISSING = "missing"


class ContextAuthority(StrEnum):
    DIRECTOR = "director"
    SOURCECADO_RECORD = "sourcecado_record"
    CONNECTOR = "connector"
    DERIVED = "derived"


class ContextSensitivity(StrEnum):
    STANDARD = "standard"
    RESTRICTED = "restricted"


@dataclass(frozen=True)
class ContextSourceRef:
    id: str
    provider: str
    locator: str
    observed_at: str
    modified_at: str | None
    fresh_until: str | None = None


@dataclass(frozen=True)
class ProjectionItem:
    id: str
    category: ContextCategory
    text: str
    tokens: int
    state: ContextState
    authority: ContextAuthority
    updated_at: str
    source_refs: tuple[ContextSourceRef, ...]
    claim_key: str | None = None
    truncated: bool = False
    person_id: str | None = None
    session_id: str | None = None
    sensitivity: ContextSensitivity = ContextSensitivity.STANDARD


@dataclass(frozen=True)
class CategoryBudget:
    category: ContextCategory
    max_tokens: int
    max_item_tokens: int


@dataclass(frozen=True)
class ProjectionPolicy:
    version: str
    total_tokens: int
    category_budgets: tuple[CategoryBudget, ...]


@dataclass(frozen=True)
class CategoryProjectionDiagnostics:
    category: ContextCategory
    selected_count: int
    omitted_count: int
    used_tokens: int
    budget_tokens: int


@dataclass(frozen=True)
class ProjectionDiagnostics:
    policy_version: str
    selected_item_ids: tuple[str, ...]
    total_tokens: int
    categories: tuple[CategoryProjectionDiagnostics, ...]
    binding_sha256: str
    content_sha256: str


DEFAULT_PROJECTION_POLICY = ProjectionPolicy(
    version="context-projection-v1-proposal",
    total_tokens=2_048,
    category_budgets=(
        CategoryBudget(ContextCategory.OPERATOR_PREFERENCE, 256, 64),
        CategoryBudget(ContextCategory.SEQUENCE_STATE, 256, 256),
        CategoryBudget(ContextCategory.PERSON_EVIDENCE, 1_024, 160),
        CategoryBudget(ContextCategory.SESSION_WORKING, 512, 128),
    ),
)


@dataclass(frozen=True)
class ProjectionIdentity:
    persona_id: str
    session_id: str
    person_id: str | None
    target: str | None
    prompt_version: str
    effective_tools_hash: str


@dataclass(frozen=True)
class PreparedContextProjection:
    identity: ProjectionIdentity
    items: tuple[ProjectionItem, ...]
    diagnostics: ProjectionDiagnostics

    def reuse_for(self, identity: ProjectionIdentity) -> "PreparedContextProjection":
        if identity != self.identity:
            raise ValueError("context projection binding mismatch")
        return self


def prepare_context_projection(
    *,
    identity: ProjectionIdentity,
    items: tuple[ProjectionItem, ...],
    policy: ProjectionPolicy = DEFAULT_PROJECTION_POLICY,
) -> PreparedContextProjection:
    for item in items:
        if item.sensitivity is ContextSensitivity.RESTRICTED:
            raise ValueError("restricted context item reached projection boundary")
        if item.state is ContextState.CONFLICTING and len(item.source_refs) < 2:
            raise ValueError(
                "conflicting context needs at least two source references"
            )
        if item.state is not ContextState.MISSING and not item.source_refs:
            raise ValueError(f"source reference is required for context item {item.id}")
        if item.category is ContextCategory.OPERATOR_PREFERENCE:
            scoped_correctly = item.person_id is None and item.session_id is None
        elif item.category in {
            ContextCategory.SEQUENCE_STATE,
            ContextCategory.PERSON_EVIDENCE,
        }:
            scoped_correctly = (
                identity.person_id is not None
                and item.person_id == identity.person_id
                and item.session_id is None
            )
        else:
            scoped_correctly = (
                item.session_id == identity.session_id
                and item.person_id in {None, identity.person_id}
            )
        if not scoped_correctly:
            raise ValueError(f"context item scope mismatch: {item.id}")
    state_order = {
        ContextState.CONFLICTING: 0,
        ContextState.MISSING: 1,
        ContextState.CURRENT: 2,
        ContextState.STALE: 3,
    }
    authority_order = {
        ContextAuthority.DIRECTOR: 0,
        ContextAuthority.SOURCECADO_RECORD: 1,
        ContextAuthority.CONNECTOR: 2,
        ContextAuthority.DERIVED: 3,
    }

    def item_key(item: ProjectionItem) -> tuple[int, int, float, str]:
        updated = datetime.fromisoformat(item.updated_at).timestamp()
        return (
            state_order[item.state],
            authority_order[item.authority],
            -updated,
            item.id,
        )

    selected: list[ProjectionItem] = []
    category_diagnostics: list[CategoryProjectionDiagnostics] = []
    total_used = 0
    for category_budget in policy.category_budgets:
        used = 0
        omitted = 0
        candidates = sorted(
            (item for item in items if item.category == category_budget.category),
            key=item_key,
        )
        for item in candidates:
            if item.tokens > category_budget.max_item_tokens:
                omitted += 1
                continue
            if used + item.tokens > category_budget.max_tokens:
                omitted += 1
                continue
            if total_used + item.tokens > policy.total_tokens:
                omitted += 1
                continue
            selected.append(item)
            used += item.tokens
            total_used += item.tokens
        category_diagnostics.append(
            CategoryProjectionDiagnostics(
                category=category_budget.category,
                selected_count=len(candidates) - omitted,
                omitted_count=omitted,
                used_tokens=used,
                budget_tokens=category_budget.max_tokens,
            )
        )
    binding_json = json.dumps(asdict(identity), sort_keys=True, separators=(",", ":"))
    content_json = json.dumps(
        [asdict(item) for item in selected],
        sort_keys=True,
        separators=(",", ":"),
    )
    diagnostics = ProjectionDiagnostics(
        policy_version=policy.version,
        selected_item_ids=tuple(item.id for item in selected),
        total_tokens=sum(item.tokens for item in selected),
        categories=tuple(category_diagnostics),
        binding_sha256=sha256(binding_json.encode("utf-8")).hexdigest(),
        content_sha256=sha256(content_json.encode("utf-8")).hexdigest(),
    )
    return PreparedContextProjection(
        identity=identity,
        items=tuple(selected),
        diagnostics=diagnostics,
    )
