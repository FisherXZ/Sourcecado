"""Every supported model has a verified or conservative context budget.

Verified means an entry in the Sourcecado-owned model table in `provider.py`,
which also carries the pricing the cost estimate uses. Conservative means no
entry: the budget falls back to a window small enough that Sourcecado compacts
early rather than discovering the real limit from a provider rejection.
"""

from __future__ import annotations

from coworker.compaction import (
    ContextSignal,
    SignalSource,
    context_signal,
    estimate_tokens,
    keep_tokens,
    should_compact,
    trigger_tokens,
)
from coworker.provider import (
    CONSERVATIVE_CONTEXT_WINDOW_TOKENS,
    BudgetConfidence,
    ContextBudget,
    context_budget,
    supported_model_budgets,
)


def test_every_supported_model_has_a_verified_budget():
    budgets = supported_model_budgets()
    assert budgets, "the model table is empty"
    for budget in budgets:
        assert budget.confidence is BudgetConfidence.VERIFIED
        assert budget.window_tokens > 0


def test_every_configured_provider_default_is_covered():
    """The four providers Sourcecado ships with must not fall back."""
    from coworker.provider import _PROVIDER_DEFAULTS

    covered = {(budget.provider, budget.model) for budget in supported_model_budgets()}
    for provider, model, _key in _PROVIDER_DEFAULTS:
        assert (provider, model) in covered, f"{provider}/{model} has no budget"


def test_an_unknown_model_is_conservative_not_optimistic():
    budget = context_budget("deepseek", "deepseek-v9-unreleased")

    assert budget.confidence is BudgetConfidence.CONSERVATIVE
    assert budget.window_tokens == CONSERVATIVE_CONTEXT_WINDOW_TOKENS
    verified = max(item.window_tokens for item in supported_model_budgets())
    assert budget.window_tokens < verified, "the fallback must not exceed a real window"


def test_an_unknown_provider_is_conservative():
    budget = context_budget("mystery", "mystery-1")
    assert budget.confidence is BudgetConfidence.CONSERVATIVE


def test_the_budget_matches_the_metadata_table():
    from coworker.provider import provider_model_metadata

    budget = context_budget("openai", "gpt-4o-mini")
    metadata = provider_model_metadata("openai", "gpt-4o-mini")

    assert budget.window_tokens == metadata.context_window_tokens
    assert budget.confidence is BudgetConfidence.VERIFIED


# --- measurement (criterion 2) ------------------------------------------


def test_provider_reported_usage_is_preferred_over_the_estimate():
    messages = [{"role": "user", "content": "x" * 40}]

    signal = context_signal(messages, reported_input_tokens=9_000)

    assert signal.source is SignalSource.PROVIDER
    assert signal.tokens == 9_000


def test_the_estimate_is_used_and_labelled_when_no_usage_was_reported():
    messages = [{"role": "user", "content": "x" * 4_000}]

    signal = context_signal(messages, reported_input_tokens=None)

    assert signal.source is SignalSource.ESTIMATE
    assert signal.tokens == estimate_tokens(messages)
    assert signal.tokens > 900  # the documented chars/4 ratio, not a stub


def test_a_stale_provider_figure_never_undercounts_what_was_appended():
    """The reported figure describes the previous request. Anything appended
    since is estimated on top, so a huge new tool result cannot hide behind a
    small stale number."""
    messages = [{"role": "user", "content": "x" * 400_000}]

    signal = context_signal(messages, reported_input_tokens=10)

    assert signal.source is SignalSource.PROVIDER
    assert signal.tokens >= estimate_tokens(messages)


def test_a_negative_provider_figure_falls_back_to_the_estimate():
    signal = context_signal([{"role": "user", "content": "hi"}], reported_input_tokens=-1)
    assert signal.source is SignalSource.ESTIMATE


# --- thresholds ----------------------------------------------------------


def test_a_conservative_budget_compacts_earlier_than_a_verified_one():
    unknown = context_budget("deepseek", "not-in-the-table")
    known = context_budget("deepseek", "deepseek-v4-pro")

    assert trigger_tokens(unknown) < trigger_tokens(known)


def test_a_huge_window_still_hits_the_cap():
    """A million-token model must not wait for a million tokens."""
    from coworker.compaction import DEFAULT_CAP_TOKENS

    budget = ContextBudget(
        provider="deepseek",
        model="deepseek-v4-pro",
        window_tokens=1_000_000,
        confidence=BudgetConfidence.VERIFIED,
    )
    assert trigger_tokens(budget) == DEFAULT_CAP_TOKENS


def test_should_compact_is_false_below_the_trigger_and_true_at_it():
    budget = context_budget("openai", "gpt-4o-mini")
    trigger = trigger_tokens(budget)

    below = ContextSignal(tokens=trigger - 1, source=SignalSource.PROVIDER)
    at = ContextSignal(tokens=trigger, source=SignalSource.PROVIDER)

    assert not should_compact(below, budget)
    assert should_compact(at, budget)


def test_the_keep_budget_is_a_real_fraction_of_the_trigger():
    budget = context_budget("openai", "gpt-4o-mini")
    assert 0 < keep_tokens(budget) < trigger_tokens(budget)
