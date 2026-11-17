"""Helpers over SEC companyfacts payloads and XBRL instance documents."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

# (value, filed, accession) for one reporting period.
Fact = tuple[float, date, str]
Period = tuple[date | None, date]

XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"


def is_quarter(start: date, end: date) -> bool:
    return 80 <= (end - start).days <= 100


def _facts(payload: dict[str, Any], tag: str, unit: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = (
        payload.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get(unit, [])
    )
    today = date.today()
    out = []
    for value in values:
        if value.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
            continue
        if not all(k in value for k in ("end", "filed", "val", "accn")):
            continue
        try:
            end = date.fromisoformat(value["end"])
            filed = date.fromisoformat(value["filed"])
            start = date.fromisoformat(value["start"]) if "start" in value else None
            val = float(value["val"])
        except (ValueError, TypeError):
            continue
        if end > today or filed > today:
            continue
        out.append({"start": start, "end": end, "filed": filed, "val": val, "accn": value["accn"]})
    return out


def _latest_revision(facts: list[dict[str, Any]]) -> dict[Period, Fact]:
    """Key facts by period with the latest revised value, cited to the first filing reporting it.

    Later filings repeat prior periods as comparatives; the original filing is
    the auditable source unless the value was restated.
    """
    best: dict[Period, Fact] = {}
    for f in sorted(facts, key=lambda x: x["filed"]):
        key = (f["start"], f["end"])
        if key not in best or f["val"] != best[key][0]:
            best[key] = (f["val"], f["filed"], f["accn"])
    return best


def instant_series(payload: dict[str, Any], tag: str, unit: str = "USD") -> dict[date, Fact]:
    return {end: fact for (_, end), fact in _latest_revision(_facts(payload, tag, unit)).items()}


def first_instant_series(payload: dict[str, Any], tags: tuple[str, ...], unit: str = "USD") -> dict[date, tuple[Fact, str]]:
    """Per period end, the value from the first tag in priority order that reports it."""
    out: dict[date, tuple[Fact, str]] = {}
    for tag in tags:
        for end, fact in instant_series(payload, tag, unit).items():
            out.setdefault(end, (fact, tag))
    return out


def quarterly_series(payload: dict[str, Any], tag: str, unit: str = "USD") -> dict[date, Fact]:
    """Three-month values per quarter end, derived from year-to-date facts when needed.

    Many 10-Qs tag only six- and nine-month totals, and 10-Ks only the full
    year; the quarter is the difference between consecutive year-to-date
    values sharing a start date.
    """
    periods = _latest_revision(_facts(payload, tag, unit))
    out: dict[date, Fact] = {}
    for (start, end), fact in periods.items():
        if start is not None and is_quarter(start, end):
            out[end] = fact
    for (start, end), (val, filed, accn) in periods.items():
        if start is None or end in out:
            continue
        for (prior_start, prior_end), (prior_val, _, _) in periods.items():
            if prior_start == start and is_quarter(prior_end, end):
                out[end] = (val - prior_val, filed, accn)
                break
    return out


def first_quarterly_series(payload: dict[str, Any], tags: tuple[str, ...], unit: str = "USD") -> dict[date, tuple[Fact, str]]:
    """Per quarter end, the value from the first tag in priority order that reports it."""
    out: dict[date, tuple[Fact, str]] = {}
    for tag in tags:
        for end, fact in quarterly_series(payload, tag, unit).items():
            out.setdefault(end, (fact, tag))
    return out


def trailing_sum(quarters: dict[date, tuple[Fact, str]], end: date, n: int = 4) -> float | None:
    """Sum of the n consecutive quarters ending at `end`, or None if any is missing."""
    ends = sorted(d for d in quarters if d <= end)[-n:]
    if len(ends) < n or ends[-1] != end:
        return None
    if any(not is_quarter(a, b) for a, b in zip(ends, ends[1:])):
        return None
    return sum(quarters[d][0][0] for d in ends)


def instance_dimensional_value(
    instance_xml: bytes,
    concept: str,
    period_end: str,
    required: dict[str, str],
    allowed: dict[str, str] | None = None,
) -> float | None:
    """Largest instant value of `concept` whose context has exactly the required
    dimension members, plus optionally the allowed ones, at `period_end`."""
    root = ET.fromstring(instance_xml)
    allowed = allowed or {}
    contexts: set[str] = set()
    for ctx in root.iter(f"{XBRLI}context"):
        instant = ctx.find(f"{XBRLI}period/{XBRLI}instant")
        if instant is None or instant.text != period_end or any(True for _ in ctx.iter(f"{XBRLDI}typedMember")):
            continue
        dims = {
            (m.get("dimension") or "").split(":")[-1]: (m.text or "").split(":")[-1]
            for m in ctx.iter(f"{XBRLDI}explicitMember")
        }
        if all(dims.get(k) == v for k, v in required.items()) and all(
            k in required or allowed.get(k) == v for k, v in dims.items()
        ):
            contexts.add(ctx.get("id") or "")
    values = []
    for el in root:
        if el.tag.split("}")[-1] == concept and el.get("contextRef") in contexts and el.text:
            try:
                values.append(float(el.text))
            except ValueError:
                continue
    return max(values) if values else None
