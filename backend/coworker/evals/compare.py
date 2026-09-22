"""Paired baseline/candidate summaries without conflating quality and cost."""

from __future__ import annotations

from dataclasses import dataclass
from math import fsum
from typing import Callable

from coworker.evals.models import EvalRunResult


@dataclass(frozen=True, slots=True)
class CorrectnessSummary:
    total_pairs: int
    eligible_pairs: int
    baseline_pass_rate: float | None
    candidate_pass_rate: float | None
    pass_rate_lift: float | None
    baseline_wins: int
    candidate_wins: int
    ties: int


@dataclass(frozen=True, slots=True)
class MetricSummary:
    total_pairs: int
    eligible_pairs: int
    baseline_mean: float | None
    candidate_mean: float | None
    mean_delta: float | None


@dataclass(frozen=True, slots=True)
class JudgeScoreSummary(MetricSummary):
    observational: bool = True


@dataclass(frozen=True, slots=True)
class PairComparison:
    baseline: str
    candidate: str
    correctness: CorrectnessSummary
    tokens: MetricSummary
    latency_ms: MetricSummary
    estimated_cost_usd: MetricSummary
    retries: MetricSummary
    compactions: MetricSummary
    judge_scores: JudgeScoreSummary


@dataclass(frozen=True, slots=True)
class ComparisonDiagnostic:
    pair_key: str
    variant_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    baseline: str
    candidates: tuple[str, ...]
    comparisons: tuple[PairComparison, ...]
    diagnostics: tuple[ComparisonDiagnostic, ...]


def _mean(values: list[float]) -> float | None:
    return fsum(values) / len(values) if values else None


def _metric(
    pairs: list[tuple[EvalRunResult, EvalRunResult]],
    *,
    total_pairs: int,
    select: Callable[[EvalRunResult], int | float | None],
) -> MetricSummary:
    baseline_values: list[float] = []
    candidate_values: list[float] = []
    for baseline, candidate in pairs:
        left = select(baseline)
        right = select(candidate)
        if left is None or right is None:
            continue
        baseline_values.append(float(left))
        candidate_values.append(float(right))
    baseline_mean = _mean(baseline_values)
    candidate_mean = _mean(candidate_values)
    return MetricSummary(
        total_pairs=total_pairs,
        eligible_pairs=len(baseline_values),
        baseline_mean=baseline_mean,
        candidate_mean=candidate_mean,
        mean_delta=(
            None
            if baseline_mean is None or candidate_mean is None
            else candidate_mean - baseline_mean
        ),
    )


def _correctness(
    pairs: list[tuple[EvalRunResult, EvalRunResult]], *, total_pairs: int
) -> CorrectnessSummary:
    baseline_passes = 0
    candidate_passes = 0
    baseline_wins = 0
    candidate_wins = 0
    ties = 0
    for baseline, candidate in pairs:
        left = baseline.passed
        right = candidate.passed
        baseline_passes += int(left)
        candidate_passes += int(right)
        if left == right:
            ties += 1
        elif left:
            baseline_wins += 1
        else:
            candidate_wins += 1
    eligible = len(pairs)
    baseline_rate = baseline_passes / eligible if eligible else None
    candidate_rate = candidate_passes / eligible if eligible else None
    return CorrectnessSummary(
        total_pairs=total_pairs,
        eligible_pairs=eligible,
        baseline_pass_rate=baseline_rate,
        candidate_pass_rate=candidate_rate,
        pass_rate_lift=(
            None
            if baseline_rate is None or candidate_rate is None
            else candidate_rate - baseline_rate
        ),
        baseline_wins=baseline_wins,
        candidate_wins=candidate_wins,
        ties=ties,
    )


def _judge(
    pairs: list[tuple[EvalRunResult, EvalRunResult]], *, total_pairs: int
) -> JudgeScoreSummary:
    metric = _metric(
        [
            pair
            for pair in pairs
            if pair[0].judge is not None
            and pair[1].judge is not None
            and pair[0].judge.judge == pair[1].judge.judge
        ],
        total_pairs=total_pairs,
        select=lambda result: result.judge.score if result.judge is not None else None,
    )
    return JudgeScoreSummary(
        total_pairs=metric.total_pairs,
        eligible_pairs=metric.eligible_pairs,
        baseline_mean=metric.baseline_mean,
        candidate_mean=metric.candidate_mean,
        mean_delta=metric.mean_delta,
        observational=True,
    )


def compare_runs(
    runs: list[EvalRunResult] | tuple[EvalRunResult, ...],
    *,
    baseline_name: str,
    candidate_names: tuple[str, ...],
) -> ComparisonReport:
    if not baseline_name:
        raise ValueError("baseline_name must be non-empty")
    if not candidate_names:
        raise ValueError("at least one candidate name is required")
    grouped: dict[tuple[str, str], list[EvalRunResult]] = {}
    pair_keys: set[str] = set()
    relevant_names = {baseline_name, *candidate_names}
    for run in runs:
        if run.variant_name not in relevant_names:
            continue
        grouped.setdefault((run.variant_name, run.pair_key), []).append(run)
        pair_keys.add(run.pair_key)
    diagnostics: list[ComparisonDiagnostic] = []
    comparisons: list[PairComparison] = []
    for candidate_name in candidate_names:
        eligible_pairs: list[tuple[EvalRunResult, EvalRunResult]] = []
        for pair_key in sorted(pair_keys):
            baseline = grouped.get((baseline_name, pair_key), [])
            candidate = grouped.get((candidate_name, pair_key), [])
            for variant_name, observations in (
                (baseline_name, baseline),
                (candidate_name, candidate),
            ):
                if not observations:
                    diagnostics.append(
                        ComparisonDiagnostic(pair_key, variant_name, "missing-run")
                    )
                elif len(observations) > 1:
                    diagnostics.append(
                        ComparisonDiagnostic(pair_key, variant_name, "duplicate-run")
                    )
                elif observations[0].infrastructure_error is not None:
                    diagnostics.append(
                        ComparisonDiagnostic(
                            pair_key, variant_name, "infrastructure-error"
                        )
                    )
            if (
                len(baseline) == 1
                and len(candidate) == 1
                and baseline[0].infrastructure_error is None
                and candidate[0].infrastructure_error is None
            ):
                eligible_pairs.append((baseline[0], candidate[0]))
                if (
                    baseline[0].judge is not None
                    and candidate[0].judge is not None
                    and baseline[0].judge.judge != candidate[0].judge.judge
                ):
                    diagnostics.append(
                        ComparisonDiagnostic(
                            pair_key,
                            candidate_name,
                            "judge-contract-mismatch",
                        )
                    )
        total_pairs = len(pair_keys)
        comparisons.append(
            PairComparison(
                baseline=baseline_name,
                candidate=candidate_name,
                correctness=_correctness(eligible_pairs, total_pairs=total_pairs),
                tokens=_metric(
                    eligible_pairs,
                    total_pairs=total_pairs,
                    select=lambda result: result.measurements.total_tokens,
                ),
                latency_ms=_metric(
                    eligible_pairs,
                    total_pairs=total_pairs,
                    select=lambda result: result.measurements.latency_ms,
                ),
                estimated_cost_usd=_metric(
                    eligible_pairs,
                    total_pairs=total_pairs,
                    select=lambda result: result.measurements.estimated_cost_usd,
                ),
                retries=_metric(
                    eligible_pairs,
                    total_pairs=total_pairs,
                    select=lambda result: result.measurements.retry_count,
                ),
                compactions=_metric(
                    eligible_pairs,
                    total_pairs=total_pairs,
                    select=lambda result: result.measurements.compaction_count,
                ),
                judge_scores=_judge(eligible_pairs, total_pairs=total_pairs),
            )
        )
    return ComparisonReport(
        baseline=baseline_name,
        candidates=candidate_names,
        comparisons=tuple(comparisons),
        diagnostics=tuple(
            sorted(
                set(diagnostics),
                key=lambda item: (item.pair_key, item.variant_name, item.reason),
            )
        ),
    )
