from types import SimpleNamespace

import pytest

from cost import PRICING, KnownLLMCost, compute_cost, merge_llm_cost

FEE = 1 + PRICING["openrouter_credit_fee"]


def _rate(input_tokens: int, output_tokens: int) -> float:
    return FEE * (
        input_tokens / 1e6 * PRICING["llm_input_per_mtok"]
        + output_tokens / 1e6 * PRICING["llm_output_per_mtok"]
    )


class LLMModelUsage(SimpleNamespace):
    pass


def test_merge_without_openrouter_uses_rate_table() -> None:
    cost, source = merge_llm_cost(10_000, 1_000, None)
    assert source == "rate_table"
    assert cost == pytest.approx(_rate(10_000, 1_000))
    empty = KnownLLMCost(cost_usd=0.0, input_tokens=0, output_tokens=0, generations=0)
    assert merge_llm_cost(10_000, 1_000, empty)[1] == "rate_table"


def test_merge_fully_covered_uses_openrouter_cost() -> None:
    known = KnownLLMCost(
        cost_usd=0.0004, input_tokens=10_000, output_tokens=1_000, generations=3
    )
    cost, source = merge_llm_cost(10_000, 1_000, known)
    assert source == "openrouter"
    # The real charge plus the credit-purchase fee, no rate-table part.
    assert cost == pytest.approx(0.0004 * FEE)


def test_merge_prices_uncovered_tokens_from_rate_table() -> None:
    known = KnownLLMCost(
        cost_usd=0.0004, input_tokens=8_000, output_tokens=900, generations=2
    )
    cost, source = merge_llm_cost(10_000, 1_000, known)
    assert source == "mixed"
    assert cost == pytest.approx(0.0004 * FEE + _rate(2_000, 100))


def test_merge_never_prices_negative_remainders() -> None:
    # Known generations can cover more tokens than session.usage reports (the
    # summary's calls at shutdown aren't in session.usage).
    known = KnownLLMCost(
        cost_usd=0.001, input_tokens=20_000, output_tokens=2_000, generations=5
    )
    cost, source = merge_llm_cost(10_000, 1_000, known)
    assert source == "openrouter"
    assert cost == pytest.approx(0.001 * FEE)


def test_compute_cost_keeps_row_shape_with_openrouter_cost() -> None:
    usage = SimpleNamespace(
        model_usage=[LLMModelUsage(input_tokens=100_000, output_tokens=5_000)]
    )
    known = KnownLLMCost(
        cost_usd=0.02, input_tokens=100_000, output_tokens=5_000, generations=4
    )
    without = compute_cost(usage, duration_seconds=61.234)
    with_known = compute_cost(usage, duration_seconds=61.234, known_llm=known)

    for result in (without, with_known):
        assert set(result) == {"costCents", "costBreakdown"}
        assert set(result["costBreakdown"]) == {"durationSeconds", "llm"}
        assert set(result["costBreakdown"]["llm"]) == {
            "inputTokens",
            "outputTokens",
            "costCents",
        }
    assert without["costBreakdown"]["llm"]["costCents"] == round(
        _rate(100_000, 5_000) * 100
    )
    assert with_known["costBreakdown"]["llm"]["costCents"] == round(0.02 * FEE * 100)
    assert with_known["costBreakdown"]["llm"]["inputTokens"] == 100_000
    assert with_known["costBreakdown"]["durationSeconds"] == 61.23
