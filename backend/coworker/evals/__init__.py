"""Isolated evaluation support for Sourcecado's active desktop agent."""

from coworker.evals.environment import EvalEnvironment
from coworker.evals.compare import ComparisonReport, compare_runs
from coworker.evals.models import (
    CompactionPolicy,
    EvalRunResult,
    EvalVariant,
    JudgeObservation,
    RunBudget,
)
from coworker.evals.runner import EvalRunner

__all__ = [
    "CompactionPolicy",
    "ComparisonReport",
    "EvalEnvironment",
    "EvalRunResult",
    "EvalRunner",
    "EvalVariant",
    "JudgeObservation",
    "RunBudget",
    "compare_runs",
]
