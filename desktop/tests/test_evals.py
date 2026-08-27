from pathlib import Path

import pytest


def test_eval_package_exists():
    package = Path(__file__).parents[1] / "coworker" / "evals" / "__init__.py"

    assert package.is_file()


def test_variant_controls_prompt_tools_provider_compaction_and_budget():
    from coworker.evals.models import CompactionPolicy, EvalVariant, RunBudget

    variant = EvalVariant(
        name="candidate-tools-v2",
        prompt_version="sourcing-v2",
        system_prompt="Never invent the target.",
        tool_catalog=("people_keep", "gmail_search"),
        provider="fake",
        model="scenario-v2",
        compaction=CompactionPolicy(
            enabled=True,
            threshold_messages=4,
            retain_messages=2,
        ),
        run_budget=RunBudget(max_provider_calls=5, max_total_tokens=1_000),
    )

    assert variant.name == "candidate-tools-v2"
    assert variant.prompt_version == "sourcing-v2"
    assert variant.tool_catalog == ("people_keep", "gmail_search")
    assert variant.provider == "fake"
    assert variant.model == "scenario-v2"
    assert variant.compaction.retain_messages == 2
    assert variant.run_budget.max_provider_calls == 5


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("prompt_version", ""),
        ("provider", ""),
        ("model", ""),
    ],
)
def test_variant_rejects_missing_stable_identifiers(field, value):
    from coworker.evals.models import EvalVariant

    values = {
        "name": "baseline",
        "prompt_version": "sourcing-v1",
        "system_prompt": "Be useful.",
        "tool_catalog": (),
        "provider": "fake",
        "model": "scenario-v1",
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        EvalVariant(**values)


def test_each_environment_has_unique_credential_free_state_and_connector_fakes(
    tmp_path,
):
    from coworker.evals.environment import EvalEnvironment

    first = EvalEnvironment.create(tmp_path, label="same")
    second = EvalEnvironment.create(tmp_path, label="same")
    try:
        assert first.root != second.root
        assert first.state_dir != second.state_dir
        assert first.workspace_dir != second.workspace_dir
        assert first.home_dir != second.home_dir
        assert first.store.db_path != second.store.db_path
        assert first.people.db_path != second.people.db_path
        assert first.connectors is not second.connectors
        assert first.connectors.gmail is not second.connectors.gmail
        assert first.connectors.http is not second.connectors.http
        assert first.credential_environment["CLUB_STATE_DIR"] == str(first.state_dir)
        assert first.credential_environment["HOME"] == str(first.home_dir)
        assert not any(
            "KEY" in name or "TOKEN" in name or "SECRET" in name
            for name in first.credential_environment
        )
        assert first.workspace_grant["path"] == str(first.workspace_dir.resolve())
        assert first.workspace_grant["access"] == "read_write"
        assert first.workspace_grant["allow_shell"] is False
        assert first.workspace_runtime.grants.list_active() == [first.workspace_grant]
        assert first.state_dir.is_dir()
        assert first.workspace_dir.is_dir()
        assert first.home_dir.is_dir()
    finally:
        first.close()
        second.close()


def test_run_local_workspace_grant_allows_effects_and_rejects_escape(tmp_path):
    from coworker.evals.environment import EvalEnvironment

    environment = EvalEnvironment.create(tmp_path, label="workspace")
    outside = environment.root / "outside.txt"
    outside.write_text("host sentinel")
    try:
        ok, created = environment.workspace_runtime.execute_tool(
            "fs_write",
            {
                "grant_id": environment.workspace_grant["id"],
                "path": "notes/eval.txt",
                "content": "inside only",
                "create_parents": True,
            },
            actor="assistant",
            session_id="eval-workspace",
            run_id="run-workspace",
        )
        escaped, escape_result = environment.workspace_runtime.execute_tool(
            "fs_read",
            {
                "grant_id": environment.workspace_grant["id"],
                "path": "../outside.txt",
            },
            actor="assistant",
            session_id="eval-workspace",
            run_id="run-workspace",
        )
        absolute, absolute_result = environment.workspace_runtime.execute_tool(
            "fs_read",
            {
                "grant_id": environment.workspace_grant["id"],
                "path": str(outside.resolve()),
            },
            actor="assistant",
            session_id="eval-workspace",
            run_id="run-workspace",
        )

        assert ok, created
        assert (environment.workspace_dir / "notes" / "eval.txt").read_text() == (
            "inside only"
        )
        assert not escaped
        assert "escape" in str(escape_result).lower() or "outside" in str(
            escape_result
        ).lower()
        assert not absolute
        assert "relative" in str(absolute_result).lower()
        assert outside.read_text() == "host sentinel"
    finally:
        environment.close()


def test_fake_scenario_asserts_native_artifacts_effects_terminal_and_telemetry(
    tmp_path,
):
    from coworker.evals.models import EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario

    result = EvalRunner(tmp_path).run_fake(
        keep_person_scenario(),
        EvalVariant(
            name="baseline",
            prompt_version="sourcing-v1",
            system_prompt="Keep only the people the director names.",
            tool_catalog=("people_keep",),
            provider="fake",
            model="sourcecado-scenario-v1",
        ),
        repetition=1,
    )

    assert result.passed
    assert result.execution_mode == "fake"
    assert result.nondeterministic is False
    assert result.tool_sequence == ("people_keep",)
    assert result.terminal_state == "complete"
    assert result.persisted_effects["people"][0]["first_name"] == "Alyssa"
    assert result.session_artifact["session"]["session_id"] == result.session_id
    assert [row["role"] for row in result.session_artifact["messages"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert {row["record_type"] for row in result.telemetry} == {
        "span_started",
        "span_event",
        "span_settled",
    }
    assert result.measurements.total_tokens == 44
    assert result.measurements.estimated_cost_usd == pytest.approx(0.0000044)
    assert result.measurements.retry_count == 0
    assert result.measurements.compaction_count == 0
    assert Path(result.artifacts.conversation_jsonl).is_file()
    assert Path(result.artifacts.events_jsonl).is_file()
    assert Path(result.artifacts.telemetry_jsonl).is_file()
    assert Path(result.artifacts.result_json).is_file()


def test_invariant_violation_is_a_hard_deterministic_failure(tmp_path):
    from dataclasses import replace

    from coworker.evals.models import EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario

    scenario = replace(
        keep_person_scenario(),
        expected_tool_sequence=("people_keep", "gmail_search"),
    )
    result = EvalRunner(tmp_path).run_fake(
        scenario,
        EvalVariant(
            name="broken-candidate",
            prompt_version="sourcing-v1",
            system_prompt="Keep people.",
            tool_catalog=("people_keep", "gmail_search"),
            provider="fake",
            model="sourcecado-scenario-v1",
        ),
        repetition=1,
    )

    assert not result.passed
    failures = [item for item in result.invariants if not item.passed]
    assert [(item.name, item.kind) for item in failures] == [
        ("tool_sequence", "deterministic")
    ]
    assert "gmail_search" in failures[0].detail
    assert result.infrastructure_error is None


def test_compaction_and_budget_policy_are_applied_and_reported(tmp_path):
    from dataclasses import replace

    from coworker.evals.models import CompactionPolicy, EvalVariant, RunBudget
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario

    scenario = replace(
        keep_person_scenario(),
        initial_messages=tuple(
            {"role": "user", "content": f"prior {index}"} for index in range(5)
        ),
    )
    result = EvalRunner(tmp_path).run_fake(
        scenario,
        EvalVariant(
            name="compact",
            prompt_version="sourcing-v2",
            system_prompt="Compact deliberately.",
            tool_catalog=("people_keep",),
            provider="fake",
            model="sourcecado-scenario-v2",
            compaction=CompactionPolicy(
                enabled=True,
                threshold_messages=4,
                retain_messages=2,
            ),
            run_budget=RunBudget(max_provider_calls=2, max_total_tokens=100),
        ),
        repetition=2,
    )

    assert result.passed
    assert result.measurements.compaction_count == 1
    assert result.provider_calls == 2
    assert result.variant["run_budget"] == {
        "max_provider_calls": 2,
        "max_total_tokens": 100,
    }
    assert result.variant["compaction"]["enabled"] is True
    assert result.session_artifact["messages"][0]["content"].startswith(
        "[eval compaction]"
    )
    compaction = next(
        record["event"]
        for record in result.telemetry
        if record["record_type"] == "span_event"
        and record["event"]["event_type"] == "compaction"
    )
    assert compaction["input_tokens"] is None
    assert compaction["output_tokens"] is None


def test_multibyte_compaction_does_not_report_character_lengths_as_tokens(tmp_path):
    from dataclasses import replace

    from coworker.evals.models import CompactionPolicy, EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario

    content = "你好🥑"
    scenario = replace(
        keep_person_scenario(),
        initial_messages=tuple(
            {"role": "user", "content": content} for _ in range(3)
        ),
    )
    result = EvalRunner(tmp_path).run_fake(
        scenario,
        EvalVariant(
            name="multibyte-compaction",
            prompt_version="v1",
            system_prompt="Do not invent token counts.",
            tool_catalog=("people_keep",),
            provider="fake",
            model="scenario-v1",
            compaction=CompactionPolicy(
                enabled=True,
                threshold_messages=3,
                retain_messages=1,
            ),
        ),
        repetition=1,
    )

    compaction = next(
        record["event"]
        for record in result.telemetry
        if record["record_type"] == "span_event"
        and record["event"]["event_type"] == "compaction"
    )
    assert compaction["input_tokens"] is None
    assert compaction["output_tokens"] is None
    assert compaction["input_tokens"] != len(content) * 3


def test_compaction_retains_a_complete_assistant_tool_group_atomically(tmp_path):
    from dataclasses import replace

    from coworker.evals.models import CompactionPolicy, EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario

    assistant = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "prior-c1",
                "type": "function",
                "function": {"name": "now", "arguments": "{}"},
            }
        ],
    }
    tool = {
        "role": "tool",
        "name": "now",
        "tool_call_id": "prior-c1",
        "content": '{"iso":"earlier"}',
    }
    scenario = replace(
        keep_person_scenario(),
        initial_messages=(
            {"role": "user", "content": "Earlier request"},
            assistant,
            tool,
        ),
    )
    result = EvalRunner(tmp_path).run_fake(
        scenario,
        EvalVariant(
            name="atomic-compaction",
            prompt_version="v1",
            system_prompt="Keep complete tool groups.",
            tool_catalog=("people_keep",),
            provider="fake",
            model="scenario-v1",
            compaction=CompactionPolicy(
                enabled=True,
                threshold_messages=3,
                retain_messages=1,
            ),
        ),
        repetition=1,
    )

    compacted = result.session_artifact["messages"][:3]
    assert [message["role"] for message in compacted] == [
        "user",
        "assistant",
        "tool",
    ]
    assert compacted[1]["tool_calls"][0]["id"] == "prior-c1"
    assert compacted[2]["tool_call_id"] == "prior-c1"
    transcript = next(
        item for item in result.invariants if item.name == "transcript_integrity"
    )
    assert transcript.passed
    assert result.passed


def test_compaction_drops_an_incomplete_open_tool_group(tmp_path):
    from dataclasses import replace

    from coworker.evals.models import CompactionPolicy, EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario

    scenario = replace(
        keep_person_scenario(),
        initial_messages=(
            {"role": "user", "content": "Earlier request"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "open-c1",
                        "type": "function",
                        "function": {"name": "now", "arguments": "{}"},
                    }
                ],
            },
        ),
    )
    result = EvalRunner(tmp_path).run_fake(
        scenario,
        EvalVariant(
            name="drop-open-compaction",
            prompt_version="v1",
            system_prompt="Drop incomplete tool groups.",
            tool_catalog=("people_keep",),
            provider="fake",
            model="scenario-v1",
            compaction=CompactionPolicy(
                enabled=True,
                threshold_messages=2,
                retain_messages=1,
            ),
        ),
        repetition=1,
    )

    messages = result.session_artifact["messages"]
    assert not any(
        call.get("id") == "open-c1"
        for message in messages
        for call in message.get("tool_calls") or []
    )
    assert not any(message.get("tool_call_id") == "open-c1" for message in messages)
    assert next(
        item for item in result.invariants if item.name == "transcript_integrity"
    ).passed


def test_fake_provider_rejects_an_orphan_tool_result_transcript(tmp_path):
    from dataclasses import replace

    from coworker.evals.models import EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario

    scenario = replace(
        keep_person_scenario(),
        initial_messages=(
            {
                "role": "tool",
                "name": "now",
                "tool_call_id": "orphan-c1",
                "content": "{}",
            },
        ),
    )
    result = EvalRunner(tmp_path).run_fake(
        scenario,
        EvalVariant(
            name="orphan-transcript",
            prompt_version="v1",
            system_prompt="Reject malformed transcripts.",
            tool_catalog=("people_keep",),
            provider="fake",
            model="scenario-v1",
        ),
        repetition=1,
    )

    transcript = next(
        item for item in result.invariants if item.name == "transcript_integrity"
    )
    assert not transcript.passed
    assert "orphan" in transcript.detail
    assert not result.passed


def test_unknown_tool_catalog_fails_before_running(tmp_path):
    from coworker.evals.models import EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario

    with pytest.raises(ValueError, match="unknown eval tool"):
        EvalRunner(tmp_path).run_fake(
            keep_person_scenario(),
            EvalVariant(
                name="unknown-tool",
                prompt_version="sourcing-v1",
                system_prompt="",
                tool_catalog=("invented_tool",),
                provider="fake",
                model="sourcecado-scenario-v1",
            ),
            repetition=1,
        )


def test_scripted_tool_outside_active_catalog_fails_before_execution(tmp_path):
    from coworker.evals.models import EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import EvalScenario, ProviderStep
    from coworker.provider import ToolCall

    scenario = EvalScenario(
        scenario_id="catalog-enforcement",
        prompt="What time is it?",
        provider_steps=(
            ProviderStep(
                tool_calls=(ToolCall(id="catalog-c1", name="now", arguments={}),)
            ),
        ),
        expected_tool_sequence=(),
        expected_terminal_state="failed",
    )
    result = EvalRunner(tmp_path).run_fake(
        scenario,
        EvalVariant(
            name="empty-catalog",
            prompt_version="v1",
            system_prompt="No tools are available.",
            tool_catalog=(),
            provider="fake",
            model="scenario-v1",
        ),
        repetition=1,
    )

    assert result.tool_sequence == ()
    catalog = next(item for item in result.invariants if item.name == "tool_catalog")
    assert not catalog.passed
    assert "now" in catalog.detail
    assert result.infrastructure_error is None
    assert not result.passed


def test_comparison_pairs_repetitions_and_keeps_efficiency_metrics_separate(
    tmp_path,
):
    from dataclasses import replace

    from coworker.evals.compare import compare_runs
    from coworker.evals.models import EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario

    runner = EvalRunner(tmp_path)
    scenario = keep_person_scenario()
    baseline = EvalVariant(
        name="baseline",
        prompt_version="sourcing-v1",
        system_prompt="Keep people.",
        tool_catalog=("people_keep",),
        provider="fake",
        model="sourcecado-scenario-v1",
    )
    candidate = replace(
        baseline,
        name="candidate",
        prompt_version="sourcing-v2",
        system_prompt="Keep only named people.",
    )
    runs = [
        runner.run_fake(scenario, baseline, repetition=1),
        runner.run_fake(scenario, candidate, repetition=1),
        runner.run_fake(scenario, baseline, repetition=2),
        runner.run_fake(
            replace(
                scenario,
                expected_tool_sequence=("people_keep", "gmail_search"),
            ),
            candidate,
            repetition=2,
        ),
    ]

    report = compare_runs(
        runs,
        baseline_name="baseline",
        candidate_names=("candidate",),
    )

    comparison = report.comparisons[0]
    assert (comparison.baseline, comparison.candidate) == (
        "baseline",
        "candidate",
    )
    assert comparison.correctness.total_pairs == 2
    assert comparison.correctness.eligible_pairs == 2
    assert comparison.correctness.baseline_pass_rate == 1.0
    assert comparison.correctness.candidate_pass_rate == 0.5
    assert comparison.correctness.pass_rate_lift == -0.5
    assert comparison.tokens.eligible_pairs == 2
    assert comparison.latency_ms.eligible_pairs == 2
    assert comparison.estimated_cost_usd.eligible_pairs == 2
    assert comparison.retries.eligible_pairs == 2
    assert comparison.compactions.eligible_pairs == 2
    assert report.diagnostics == ()


def test_low_nondeterministic_judge_score_is_observational_not_a_gate(tmp_path):
    from dataclasses import replace

    from coworker.evals.compare import compare_runs
    from coworker.evals.models import EvalVariant, JudgeObservation
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario

    runner = EvalRunner(tmp_path)
    baseline = EvalVariant(
        name="baseline",
        prompt_version="sourcing-v1",
        system_prompt="Keep people.",
        tool_catalog=("people_keep",),
        provider="fake",
        model="sourcecado-scenario-v1",
    )
    candidate = replace(baseline, name="candidate", prompt_version="sourcing-v2")
    baseline_run = runner.run_fake(
        keep_person_scenario(),
        baseline,
        repetition=1,
        judge=JudgeObservation(judge="style-v1", score=0.9),
    )
    candidate_run = runner.run_fake(
        keep_person_scenario(),
        candidate,
        repetition=1,
        judge=JudgeObservation(
            judge="style-v1",
            score=0.1,
            rationale="Too generic.",
        ),
    )

    report = compare_runs(
        [baseline_run, candidate_run],
        baseline_name="baseline",
        candidate_names=("candidate",),
    )

    assert candidate_run.passed
    assert report.comparisons[0].correctness.candidate_pass_rate == 1.0
    assert report.comparisons[0].judge_scores.observational is True
    assert report.comparisons[0].judge_scores.candidate_mean == 0.1
    assert report.comparisons[0].judge_scores.mean_delta == pytest.approx(-0.8)


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf"), -0.1, 1.1])
def test_judge_observation_requires_a_finite_unit_score(score):
    from coworker.evals.models import JudgeObservation

    with pytest.raises(ValueError, match="finite.*0.*1"):
        JudgeObservation(judge="style-v1", score=score)


def test_comparison_excludes_mismatched_judge_contracts_with_diagnostic(tmp_path):
    from dataclasses import replace

    from coworker.evals.compare import compare_runs
    from coworker.evals.models import EvalVariant, JudgeObservation
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario

    runner = EvalRunner(tmp_path)
    baseline = EvalVariant(
        name="baseline",
        prompt_version="v1",
        system_prompt="Keep people.",
        tool_catalog=("people_keep",),
        provider="fake",
        model="scenario-v1",
    )
    candidate = replace(baseline, name="candidate", prompt_version="v2")
    left = runner.run_fake(
        keep_person_scenario(),
        baseline,
        repetition=1,
        judge=JudgeObservation(judge="style-v1", score=0.9),
    )
    right = runner.run_fake(
        keep_person_scenario(),
        candidate,
        repetition=1,
        judge=JudgeObservation(judge="unrelated-v9", score=0.1),
    )

    report = compare_runs(
        [left, right],
        baseline_name="baseline",
        candidate_names=("candidate",),
    )

    assert report.comparisons[0].correctness.eligible_pairs == 1
    assert report.comparisons[0].judge_scores.eligible_pairs == 0
    assert report.comparisons[0].judge_scores.mean_delta is None
    assert report.diagnostics == (
        report.diagnostics[0],
    )
    assert report.diagnostics[0].reason == "judge-contract-mismatch"


def test_live_run_requires_opt_in_and_records_nondeterministic_identity(tmp_path):
    from coworker.evals.models import EvalVariant
    from coworker.evals.runner import (
        EvalRunner,
        LiveRunNotAuthorized,
        ScenarioProvider,
    )
    from coworker.evals.scenarios import keep_person_scenario

    scenario = keep_person_scenario()
    variant = EvalVariant(
        name="live-candidate",
        prompt_version="sourcing-live-v3",
        system_prompt="Keep the named person.",
        tool_catalog=("people_keep",),
        provider="live-test-provider",
        model="live-test-model",
    )
    provider = ScenarioProvider(variant=variant, steps=scenario.provider_steps)
    runner = EvalRunner(tmp_path)

    with pytest.raises(LiveRunNotAuthorized, match="explicit opt-in"):
        runner.run_live(
            scenario,
            variant,
            provider=provider,
            repetition=1,
            opt_in=False,
        )

    result = runner.run_live(
        scenario,
        variant,
        provider=provider,
        repetition=1,
        opt_in=True,
    )

    assert result.passed
    assert result.execution_mode == "live"
    assert result.nondeterministic is True
    assert result.provider == "live-test-provider"
    assert result.model == "live-test-model"
    assert result.prompt_version == "sourcing-live-v3"


def test_spawned_run_hides_host_secrets_from_provider_and_tool_probes(
    tmp_path, monkeypatch
):
    from coworker.evals.models import EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import environment_probe_scenario

    monkeypatch.setenv("OPENAI_API_KEY", "planted-host-key")
    monkeypatch.setenv("SESSION_TOKEN", "planted-host-token")
    variant = EvalVariant(
        name="credential-probe",
        prompt_version="probe-v1",
        system_prompt="Run the environment probe.",
        tool_catalog=("mcp__eval__environment_probe",),
        provider="fake",
        model="sourcecado-probe-v1",
    )

    result = EvalRunner(tmp_path).run_fake(
        environment_probe_scenario(), variant, repetition=1
    )

    assert result.passed
    assert result.execution_environment["provider_sensitive_keys"] == []
    assert result.execution_environment["tool_sensitive_keys"] == []
    assert "OPENAI_API_KEY" not in result.execution_environment["applied"]
    assert "SESSION_TOKEN" not in result.execution_environment["applied"]
    assert result.execution_environment["process_isolated"] is True
    assert result.execution_environment["workspace_grant"]["path"] == (
        result.artifacts.workspace_dir
    )
    assert result.execution_environment["workspace_grant"]["allow_shell"] is False


def test_workspace_scenario_writes_only_through_run_local_grant(tmp_path):
    from coworker.evals.models import EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import workspace_write_scenario

    result = EvalRunner(tmp_path).run_fake(
        workspace_write_scenario(),
        EvalVariant(
            name="workspace",
            prompt_version="workspace-v1",
            system_prompt="Write only inside the granted workspace.",
            tool_catalog=("fs_write",),
            provider="fake",
            model="sourcecado-workspace-v1",
        ),
        repetition=1,
    )

    assert result.passed
    assert result.tool_sequence == ("fs_write",)
    assert result.persisted_effects["workspace_files"] == [
        {"path": "notes/eval.txt", "content": "isolated workspace effect"}
    ]
    assert Path(result.artifacts.workspace_dir, "notes", "eval.txt").read_text() == (
        "isolated workspace effect"
    )


def test_unexpected_extra_person_is_enumerated_and_fails_exact_effect_set(tmp_path):
    from dataclasses import replace

    from coworker.evals.models import EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario
    from coworker.provider import ToolCall

    scenario = keep_person_scenario()
    original_call = scenario.provider_steps[0].tool_calls[0]
    extra = {
        "apolloId": "eval-invented",
        "firstName": "Invented",
        "lastNameObfuscated": "P***n",
        "title": "Unknown",
        "organizationName": "Outside scope",
    }
    first_step = replace(
        scenario.provider_steps[0],
        tool_calls=(
            ToolCall(
                id=original_call.id,
                name=original_call.name,
                arguments={
                    **original_call.arguments,
                    "people": [*original_call.arguments["people"], extra],
                },
            ),
        ),
    )
    result = EvalRunner(tmp_path).run_fake(
        replace(
            scenario,
            provider_steps=(first_step, *scenario.provider_steps[1:]),
        ),
        EvalVariant(
            name="extra-person",
            prompt_version="v1",
            system_prompt="Keep only Alyssa.",
            tool_catalog=("people_keep",),
            provider="fake",
            model="scenario-v1",
        ),
        repetition=1,
    )

    assert {person["apollo_id"] for person in result.persisted_effects["people"]} == {
        "eval-alyssa",
        "eval-invented",
    }
    effect_set = next(
        item for item in result.invariants if item.name == "people_effect_set"
    )
    assert not effect_set.passed
    assert "eval-invented" in effect_set.detail
    assert not result.passed


def test_unexpected_workspace_file_is_enumerated_and_fails_exact_effect_set(
    tmp_path,
):
    from dataclasses import replace

    from coworker.evals.models import EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import workspace_write_scenario
    from coworker.provider import ToolCall

    scenario = workspace_write_scenario()
    expected_call = scenario.provider_steps[0].tool_calls[0]
    extra_call = ToolCall(
        id="eval-workspace-extra",
        name="fs_write",
        arguments={
            "grant_id": "$EVAL_WORKSPACE_GRANT",
            "path": "extra/unexpected.txt",
            "content": "unintended effect",
            "create_parents": True,
        },
    )
    first_step = replace(
        scenario.provider_steps[0],
        tool_calls=(expected_call, extra_call),
    )
    result = EvalRunner(tmp_path).run_fake(
        replace(
            scenario,
            provider_steps=(first_step, *scenario.provider_steps[1:]),
            expected_tool_sequence=("fs_write", "fs_write"),
        ),
        EvalVariant(
            name="extra-workspace-effect",
            prompt_version="v1",
            system_prompt="Write only the requested note.",
            tool_catalog=("fs_write",),
            provider="fake",
            model="scenario-v1",
        ),
        repetition=1,
    )

    assert {item["path"] for item in result.persisted_effects["workspace_files"]} == {
        "notes/eval.txt",
        "extra/unexpected.txt",
    }
    effect_set = next(
        item for item in result.invariants if item.name == "workspace_effect_set"
    )
    assert not effect_set.passed
    assert "extra/unexpected.txt" in effect_set.detail
    assert not result.passed


def test_concurrent_repetitions_use_distinct_isolated_child_processes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from coworker.evals.models import EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario

    variant = EvalVariant(
        name="parallel",
        prompt_version="parallel-v1",
        system_prompt="Keep the named person.",
        tool_catalog=("people_keep",),
        provider="fake",
        model="sourcecado-parallel-v1",
    )

    def run(repetition: int):
        return EvalRunner(tmp_path).run_fake(
            keep_person_scenario(), variant, repetition=repetition
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, (1, 2, 3)))

    assert all(result.passed for result in results)
    assert len({result.artifacts.root for result in results}) == 3
    assert len(
        {result.execution_environment["child_pid"] for result in results}
    ) == 3
    assert all(
        result.execution_environment["process_isolated"] for result in results
    )


def test_provider_failure_is_infrastructure_and_excluded_from_paired_lift(tmp_path):
    from dataclasses import replace

    from coworker.evals.compare import compare_runs
    from coworker.evals.models import EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario

    runner = EvalRunner(tmp_path)
    baseline = EvalVariant(
        name="baseline",
        prompt_version="v1",
        system_prompt="Keep the person.",
        tool_catalog=("people_keep",),
        provider="fake",
        model="scenario-v1",
    )
    candidate = replace(baseline, name="candidate", prompt_version="v2")
    scenario = keep_person_scenario()
    failed_scenario = replace(
        scenario,
        provider_steps=(
            replace(scenario.provider_steps[0], error="provider unavailable"),
            *scenario.provider_steps[1:],
        ),
    )
    good = runner.run_fake(scenario, baseline, repetition=1)
    failed = runner.run_fake(failed_scenario, candidate, repetition=1)

    report = compare_runs(
        [good, failed],
        baseline_name="baseline",
        candidate_names=("candidate",),
    )

    assert failed.infrastructure_error == "agent run ended with infrastructure error"
    assert not failed.passed
    assert report.comparisons[0].correctness.total_pairs == 1
    assert report.comparisons[0].correctness.eligible_pairs == 0
    assert report.diagnostics == (
        report.diagnostics[0],
    )
    assert report.diagnostics[0].reason == "infrastructure-error"


def test_provider_failure_artifacts_do_not_capture_raw_secret_bearing_errors(tmp_path):
    from dataclasses import replace

    from coworker.evals.models import EvalVariant
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario

    scenario = keep_person_scenario()
    failed = replace(
        scenario,
        provider_steps=(
            replace(
                scenario.provider_steps[0],
                error="provider leaked sk-live-PLANTED-EVAL-SECRET",
            ),
            *scenario.provider_steps[1:],
        ),
    )
    result = EvalRunner(tmp_path).run_fake(
        failed,
        EvalVariant(
            name="safe-error",
            prompt_version="v1",
            system_prompt="Keep the person.",
            tool_catalog=("people_keep",),
            provider="fake",
            model="scenario-v1",
        ),
        repetition=1,
    )

    assert "PLANTED-EVAL-SECRET" not in Path(result.artifacts.result_json).read_text()
    assert "PLANTED-EVAL-SECRET" not in Path(result.artifacts.events_jsonl).read_text()


def test_run_budget_wraps_an_arbitrary_provider_at_the_execution_boundary(tmp_path):
    from coworker.evals.models import EvalVariant, RunBudget
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario
    from coworker.provider import FakeProvider

    scenario = keep_person_scenario()
    provider = FakeProvider(
        steps=[
            {"tool_calls": list(scenario.provider_steps[0].tool_calls)},
            {"deltas": ("Finished without respecting the budget.",)},
        ]
    )
    variant = EvalVariant(
        name="arbitrary-provider",
        prompt_version="budget-v1",
        system_prompt="Keep the named person.",
        tool_catalog=("people_keep",),
        provider="fake",
        model="fake",
        run_budget=RunBudget(max_provider_calls=1),
    )

    result = EvalRunner(tmp_path)._run_local(
        scenario,
        variant,
        repetition=1,
        provider=provider,
        execution_mode="live",
        nondeterministic=True,
        judge=None,
        process_isolated=False,
    )

    run_budget = [item for item in result.invariants if item.name == "run_budget"]
    assert len(run_budget) == 1
    assert not run_budget[0].passed
    assert "provider calls 2 exceed budget 1" in run_budget[0].detail
    assert result.provider_calls == 2
    assert result.infrastructure_error is None


def test_live_provider_token_budget_fails_as_deterministic_policy(tmp_path):
    from coworker.evals.models import EvalVariant, RunBudget
    from coworker.evals.runner import EvalRunner
    from coworker.evals.scenarios import keep_person_scenario
    from coworker.provider import FakeProvider

    scenario = keep_person_scenario()
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": list(scenario.provider_steps[0].tool_calls),
                "usage": scenario.provider_steps[0].usage,
            }
        ]
    )
    variant = EvalVariant(
        name="live-budget",
        prompt_version="budget-v1",
        system_prompt="Keep the named person.",
        tool_catalog=("people_keep",),
        provider="fake",
        model="fake",
        run_budget=RunBudget(max_provider_calls=8, max_total_tokens=1),
    )

    result = EvalRunner(tmp_path).run_live(
        scenario,
        variant,
        provider=provider,
        repetition=1,
        opt_in=True,
    )

    run_budget = [item for item in result.invariants if item.name == "run_budget"]
    assert len(run_budget) == 1
    assert not run_budget[0].passed
    assert "total tokens" in run_budget[0].detail
    assert result.infrastructure_error is None


def test_cli_runs_paired_fake_baseline_and_candidate_with_one_command(
    tmp_path, capsys
):
    import json

    from coworker.evals.__main__ import main

    artifact_root = tmp_path / "artifacts"
    exit_code = main(
        [
            "--artifacts",
            str(artifact_root),
            "--repetitions",
            "2",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "baseline -> candidate" in captured.out
    assert "pass-rate lift" in captured.out
    assert "may contain prompts" in captured.err
    summary = json.loads((artifact_root / "summary.json").read_text())
    assert summary["mode"] == "fake"
    assert len(summary["runs"]) == 4
    assert summary["comparison"]["baseline"] == "baseline"
    assert summary["comparison"]["candidates"] == ["candidate"]
    assert summary["comparison"]["comparisons"][0]["tokens"][
        "eligible_pairs"
    ] == 2


def test_repository_command_docs_and_ignore_protect_sensitive_eval_artifacts():
    root = Path(__file__).parents[2]

    assert "\neval:\n" in (root / "Makefile").read_text()
    docs = (root / "desktop" / "docs" / "evaluations.md").read_text()
    assert "make eval" in docs
    assert "prompts, responses, tool arguments, and tool output" in docs
    assert "desktop/.eval-artifacts/" in (root / ".gitignore").read_text()


def test_eval_artifact_directories_and_files_are_private(tmp_path):
    import stat

    from coworker.evals.__main__ import main

    artifact_root = tmp_path / "artifacts"
    assert main(["--artifacts", str(artifact_root)]) == 0

    directories = [artifact_root, *sorted(path for path in artifact_root.rglob("*") if path.is_dir())]
    files = sorted(path for path in artifact_root.rglob("*") if path.is_file())
    assert directories
    assert files
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in directories)
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)


def test_artifact_setup_preserves_parent_mode_and_rejects_existing_broad_target(
    tmp_path,
):
    import os
    import stat

    from coworker.evals.environment import EvalEnvironment

    caller_parent = tmp_path / "caller-owned"
    caller_parent.mkdir()
    os.chmod(caller_parent, 0o755)
    environment = EvalEnvironment.create(
        caller_parent / "private-evals", label="private-child"
    )
    try:
        assert stat.S_IMODE(caller_parent.stat().st_mode) == 0o755
        assert stat.S_IMODE((caller_parent / "private-evals").stat().st_mode) == 0o700
    finally:
        environment.close()

    with pytest.raises(ValueError, match="private artifact root"):
        EvalEnvironment.create(caller_parent, label="must-not-chmod-parent")
    assert stat.S_IMODE(caller_parent.stat().st_mode) == 0o755


def test_summary_writer_refuses_symlink_without_touching_external_target(tmp_path):
    import os

    from coworker.evals.__main__ import main

    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    sentinel = tmp_path / "outside-sentinel.txt"
    sentinel.write_text("must remain intact")
    os.symlink(sentinel, artifact_root / "summary.json")

    with pytest.raises(ValueError, match="symlink"):
        main(["--artifacts", str(artifact_root)])

    assert sentinel.read_text() == "must remain intact"
    assert (artifact_root / "summary.json").is_symlink()
