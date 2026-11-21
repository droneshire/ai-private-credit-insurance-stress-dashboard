from datetime import date

import pytest

from release_text import parse_non_accrual, parse_rbc, quarter_end_before

# Phrasings taken from the Q3 2026 earnings releases of each BDC.
NON_ACCRUAL_CASES = [
    (
        "As of September 30, 2026 and December 31, 2025, the weighted average grade of the portfolio at fair value "
        "was 3.1 and 3.1, respectively, and loans on non-accrual status represented 2.4% of the total investments "
        "at amortized cost (or 1.4% at fair value) and 1.8% at amortized cost (or 1.2% at fair value), respectively.",
        2.4, 1.4,
    ),
    (
        "As of September 30, 2026 , investments on non-accrual status comprised 0.9% of the total investment "
        "portfolio at fair value and 3.5% at cost , and our total portfolio investments at fair value were 117%.",
        3.5, 0.9,
    ),
    (
        "As of September 30, 2026, investments on non-accrual status represented 3.4% and 6.4% of the total "
        "investment portfolio at fair value and amortized cost, respectively, compared to 3.8% and 7.1%.",
        6.4, 3.4,
    ),
    (
        "• Investments on non-accrual represented 2.9% and 0.8% of the portfolio at cost and fair value, "
        "respectively, as compared to 2.8% and 0.8% as of June 30, 2026 • Repurchased $35 million",
        2.9, 0.8,
    ),
]


@pytest.mark.parametrize(("text", "cost", "fair"), NON_ACCRUAL_CASES)
def test_parse_non_accrual_phrasings(text: str, cost: float, fair: float) -> None:
    found = parse_non_accrual(text)
    assert found is not None
    assert (found.at_cost_pct, found.at_fair_value_pct) == (cost, fair)
    assert "non-accrual" in found.quote


def test_parse_non_accrual_ignores_unrelated_mentions() -> None:
    text = ("Of the approximately $2.5 billion of exited investment commitments, 95% were floating rate and "
            "3% were on non-accrual status. For more information on loans on non-accrual status, see Item 2.")
    assert parse_non_accrual(text) is None


@pytest.mark.parametrize(("text", "value", "kind"), [
    ('• Estimated combined risk-based capital ("RBC") ratio between 415% and 435%; holding company', 425.0,
     "range midpoint"),
    ("with total adjusted capital of $5.9 billion, and an estimated risk-based capital (RBC) ratio at Jackson "
     "National Life Insurance Company (JNL) of 524% ▪ Free capital", 524.0, "point"),
    ("Holding company available liquidity $ 461 RBC ratio (2) >420% >420% >420% >420% >420% Book value", 420.0,
     "lower bound"),
])
def test_parse_rbc(text: str, value: float, kind: str) -> None:
    found = parse_rbc(text)
    assert found is not None
    assert (found.ratio_pct, found.kind) == (value, kind)


def test_parse_rbc_rejects_ambiguous_table_and_vague_text() -> None:
    assert parse_rbc("RBC ratio (2) >420% >430% >440%") is None
    assert parse_rbc("Life Fleet RBC ratio remains above target") is None


@pytest.mark.parametrize(("filed", "quarter_end"), [
    (date(2026, 11, 5), date(2026, 9, 30)),
    (date(2026, 2, 18), date(2025, 12, 31)),
    (date(2026, 4, 1), date(2026, 3, 31)),
])
def test_quarter_end_before(filed: date, quarter_end: date) -> None:
    assert quarter_end_before(filed) == quarter_end
