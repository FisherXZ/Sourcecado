"""Command-line entry point for Sourcecado's isolated agent evaluations."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from coworker.evals.compare import ComparisonReport, compare_runs
from coworker.evals.environment import private_artifact_root, write_private_text
from coworker.evals.models import EvalRunResult, EvalVariant
from coworker.evals.runner import ARTIFACT_WARNING, EvalRunner
from coworker.evals.scenarios import baseline_scenarios
from coworker.provider import provider_from_env


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run isolated Sourcecado agent evals")
    parser.add_argument("--artifacts", type=Path, default=Path(".eval-artifacts"))
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--baseline-name", default="baseline")
    parser.add_argument("--candidate-name", default="candidate")
    parser.add_argument("--baseline-prompt-version", default="sourcing-v1")
    parser.add_argument("--candidate-prompt-version", default="sourcing-v2")
    parser.add_argument(
        "--tools",
        default="people_keep",
        help="comma-separated active Sourcecado tool names",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="explicitly opt into nondeterministic live-provider execution",
    )
    return parser


def _tools(raw: str) -> tuple[str, ...]:
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def _fake_variants(args: argparse.Namespace) -> tuple[EvalVariant, EvalVariant]:
    catalog = _tools(args.tools)
    baseline = EvalVariant(
        name=args.baseline_name,
        prompt_version=args.baseline_prompt_version,
        system_prompt=(
            "You are Sourcecado's sourcing assistant. Keep only people the director "
            "explicitly names."
        ),
        tool_catalog=catalog,
        provider="fake",
        model="sourcecado-scenario-v1",
    )
    candidate = EvalVariant(
        name=args.candidate_name,
        prompt_version=args.candidate_prompt_version,
        system_prompt=(
            "You are Sourcecado's sourcing assistant. Never invent a target, enrich, "
            "or send. Keep only the people the director explicitly names."
        ),
        tool_catalog=catalog,
        provider="fake",
        model="sourcecado-scenario-v1",
    )
    return baseline, candidate


def _delta(metric: object) -> str:
    value = getattr(metric, "mean_delta")
    if value is None:
        return "unavailable"
    eligible = getattr(metric, "eligible_pairs")
    total = getattr(metric, "total_pairs")
    return f"{value:+.6g} ({eligible}/{total} pairs)"


def _print_comparison(report: ComparisonReport) -> None:
    for comparison in report.comparisons:
        correctness = comparison.correctness
        lift = (
            "unavailable"
            if correctness.pass_rate_lift is None
            else f"{correctness.pass_rate_lift * 100:+.1f} pp"
        )
        print(f"{comparison.baseline} -> {comparison.candidate}")
        print(
            f"  pass-rate lift: {lift} "
            f"({correctness.eligible_pairs}/{correctness.total_pairs} pairs)"
        )
        print(f"  tokens delta: {_delta(comparison.tokens)}")
        print(f"  latency-ms delta: {_delta(comparison.latency_ms)}")
        print(f"  estimated-cost-usd delta: {_delta(comparison.estimated_cost_usd)}")
        print(f"  retries delta: {_delta(comparison.retries)}")
        print(f"  compactions delta: {_delta(comparison.compactions)}")
        print(f"  judge score (observational): {_delta(comparison.judge_scores)}")


def _write_summary(
    artifact_root: Path,
    *,
    mode: str,
    runs: list[EvalRunResult],
    comparison: ComparisonReport | None,
) -> None:
    private_artifact_root(artifact_root)
    payload = {
        "warning": ARTIFACT_WARNING,
        "mode": mode,
        "runs": [asdict(run) for run in runs],
        "comparison": asdict(comparison) if comparison is not None else None,
    }
    write_private_text(
        artifact_root / "summary.json",
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.repetitions < 1:
        print("--repetitions must be positive", file=sys.stderr)
        return 2
    print(f"WARNING: {ARTIFACT_WARNING}", file=sys.stderr)
    runner = EvalRunner(args.artifacts)
    runs: list[EvalRunResult] = []
    comparison: ComparisonReport | None = None
    if args.live:
        provider = provider_from_env()
        if provider is None:
            print(
                "No eligible live provider is configured; live eval did not run.",
                file=sys.stderr,
            )
            return 2
        variant = EvalVariant(
            name=args.candidate_name,
            prompt_version=args.candidate_prompt_version,
            system_prompt=(
                "You are Sourcecado's sourcing assistant. Never invent a target, "
                "enrich, or send. Keep only people the director explicitly names."
            ),
            tool_catalog=_tools(args.tools),
            provider=str(getattr(provider, "provider_id", "unknown")),
            model=str(getattr(provider, "model_id", "unknown")),
        )
        for repetition in range(1, args.repetitions + 1):
            for scenario in baseline_scenarios():
                runs.append(
                    runner.run_live(
                        scenario,
                        variant,
                        provider=provider,
                        repetition=repetition,
                        opt_in=True,
                    )
                )
        print(
            f"live nondeterministic runs: {len(runs)}; provider={variant.provider}; "
            f"model={variant.model}; prompt={variant.prompt_version}"
        )
    else:
        baseline, candidate = _fake_variants(args)
        for repetition in range(1, args.repetitions + 1):
            for scenario in baseline_scenarios():
                runs.append(runner.run_fake(scenario, baseline, repetition=repetition))
                runs.append(runner.run_fake(scenario, candidate, repetition=repetition))
        comparison = compare_runs(
            runs,
            baseline_name=baseline.name,
            candidate_names=(candidate.name,),
        )
        _print_comparison(comparison)
    _write_summary(
        args.artifacts,
        mode="live" if args.live else "fake",
        runs=runs,
        comparison=comparison,
    )
    return int(any(not run.passed for run in runs))


if __name__ == "__main__":
    raise SystemExit(main())
