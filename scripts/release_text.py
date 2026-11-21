"""Extract figures that issuers state only in earnings-release prose.

Each parser returns the figure together with the exact quoted passage it came
from, so every value in the dashboard can be checked against its source.
Anything that does not match a known phrasing returns None; nothing is guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

PCT = r"(\d{1,2}(?:\.\d+)?)\s?%"
COST = r"(?:amortized\s+)?cost"
FAIR = r"fair\s+value"


@dataclass(frozen=True)
class NonAccrual:
    at_cost_pct: float
    at_fair_value_pct: float
    quote: str


@dataclass(frozen=True)
class Rbc:
    ratio_pct: float
    kind: str  # "point", "range midpoint" or "lower bound"
    quote: str


def quarter_end_before(filed: date) -> date:
    """The last calendar quarter end strictly before a filing date."""
    year, month = filed.year, filed.month
    quarter_start_month = 3 * ((month - 1) // 3) + 1
    return date(year, quarter_start_month, 1) - timedelta(days=1)


def _sentence_start(text: str, pos: int, limit: int = 200) -> int:
    """Start of the sentence or bullet containing `pos`, looking back at most `limit` chars."""
    floor = max(0, pos - limit)
    breaks = [m.end() for m in re.finditer(r"(?:\.\s|•|▪|;\s)", text[floor:pos])]
    return floor + breaks[-1] if breaks else floor


def _basis(fragment: str) -> str | None:
    match = re.search(rf"({FAIR}|{COST})", fragment, re.I)
    if not match:
        return None
    return "fair" if "fair" in match.group(1).lower() else "cost"


def parse_non_accrual(text: str) -> NonAccrual | None:
    """Non-accruals as % of the portfolio at cost and at fair value.

    Handles the phrasings used by large BDCs, e.g.
    "represented 2.9% and 0.8% of the portfolio at cost and fair value, respectively",
    "represented 2.4% of the total investments at amortized cost (or 1.4% at fair value)",
    "comprised 0.9% of the total investment portfolio at fair value and 3.5% at cost".
    """
    for match in re.finditer(r"non-?\s?accrual(?:\s+status)?\s+(?:represented|comprised|were|was)", text, re.I):
        window = text[match.end(): match.end() + 260]
        window = re.split(r"\b(?:compared|as compared|versus)\b|;|\.\s", window)[0]
        pcts = list(re.finditer(PCT, window))
        if len(pcts) < 2:
            continue
        first, second = float(pcts[0].group(1)), float(pcts[1].group(1))
        tail = window[pcts[1].end():]
        between = window[pcts[0].end(): pcts[1].start()]
        if re.fullmatch(r"\s*and\s*", between) and "respectively" in tail.lower():
            bases = re.findall(rf"({FAIR}|{COST})", tail, re.I)
            if len(bases) < 2:
                continue
            order = ["fair" if "fair" in b.lower() else "cost" for b in bases[:2]]
        else:
            order = [_basis(between) or "", _basis(tail) or ""]
        if sorted(order) != ["cost", "fair"]:
            continue
        values = dict(zip(order, (first, second)))
        if not all(0 <= v <= 50 for v in values.values()):
            continue
        quote = text[_sentence_start(text, match.start()): match.end() + len(window)].strip()
        return NonAccrual(values["cost"], values["fair"], quote)
    return None


RBC_LABEL = r"(?:risk-based\s+capital|RBC)(?:\s*\(\s*\"?RBC\"?\s*\))?\s+ratio"


def parse_rbc(text: str) -> Rbc | None:
    """Insurer RBC ratio from earnings-release prose or a uniform table row."""
    patterns = (
        (rf"{RBC_LABEL}[^%.;]{{0,120}}?between\s+(\d{{3}})\s?%\s+and\s+(\d{{3}})\s?%", "range midpoint"),
        (rf"{RBC_LABEL}[^%.;]{{0,120}}?\b(?:of|was|at|is)\s+(?:approximately\s+|an\s+estimated\s+)?(\d{{3}})\s?%", "point"),
    )
    for pattern, kind in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            nums = [float(g) for g in match.groups() if g]
            value = sum(nums) / len(nums)
            if 100 <= value <= 1500:
                return Rbc(value, kind, text[_sentence_start(text, match.start()): match.end()].strip())
    # Table rows such as "RBC ratio (2) >420% >420% >420% >420% >420%": only a
    # row whose every column agrees is unambiguous without knowing column order.
    match = re.search(rf"{RBC_LABEL}\s*(?:\(\d\)\s*)?((?:>\s?\d{{3}}\s?%\s*){{2,}})", text, re.I)
    if match:
        values = set(re.findall(r"\d{3}", match.group(1)))
        if len(values) == 1:
            return Rbc(float(values.pop()), "lower bound", match.group(0).strip())
    return None
