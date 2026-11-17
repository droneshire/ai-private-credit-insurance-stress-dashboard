"""Fetch auditable SEC XBRL observations for selected BDCs.

Writes NAV per share, NAV quarter-over-quarter change and PIK interest as a
share of total investment income where the issuer tags the standard us-gaap
concepts. Non-accrual rates have no comparable us-gaap concept; they remain
blank rather than inferred from unrelated tags. Values entered by hand in
bdc_metrics.csv fill the fields SEC leaves blank.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ISSUERS = {"ARCC": 1287750, "MAIN": 1396440, "OBDC": 1655888, "FSK": 1422183}
MANAGERS = {"ARCC": "Ares", "MAIN": "Main Street (internal)", "OBDC": "Blue Owl", "FSK": "FS/KKR"}
OUT = Path("stress_dashboard_data/bdc_sec_nav.csv")
METRICS_OUT = OUT.with_name("bdc_metrics.csv")
TAG_CANDIDATES = ("NetAssetValuePerShare",)
PIK_TAG = "InterestIncomeOperatingPaidInKind"
INCOME_TAG = "GrossInvestmentIncomeOperating"
HEADERS = ("date", "ticker", "cik", "nav_per_share", "source_url", "filed_at", "tag")
METRIC_HEADERS = (
    "date", "ticker", "manager", "non_accrual_pct", "pik_income_pct",
    "nav_per_share", "nav_qoq_pct", "realized_loss_pct", "interest_coverage",
    "source_url", "notes",
)
QUARTERS_KEPT = 4

# (value, filed, accession) for one reporting period.
Fact = tuple[float, date, str]


def _filing_url(cik: int, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"


def _is_quarter(start: date, end: date) -> bool:
    return 80 <= (end - start).days <= 100


def _facts(payload: dict[str, Any], tag: str, unit: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = (
        payload.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get(unit, [])
    )
    today = date.today()
    out = []
    for value in values:
        if value.get("form") not in ("10-Q", "10-K"):
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


def _latest_revision(facts: list[dict[str, Any]]) -> dict[tuple[date | None, date], Fact]:
    """Key facts by period with the latest revised value, cited to the first filing reporting it.

    Later filings repeat prior periods as comparatives; the original filing is
    the auditable source unless the value was restated.
    """
    best: dict[tuple[date | None, date], Fact] = {}
    for f in sorted(facts, key=lambda x: x["filed"]):
        key = (f["start"], f["end"])
        if key not in best or f["val"] != best[key][0]:
            best[key] = (f["val"], f["filed"], f["accn"])
    return best


def instant_series(payload: dict[str, Any], tag: str, unit: str) -> dict[date, Fact]:
    return {end: fact for (_, end), fact in _latest_revision(_facts(payload, tag, unit)).items()}


def quarterly_series(payload: dict[str, Any], tag: str, unit: str = "USD") -> dict[date, Fact]:
    """Three-month values per quarter end, derived from year-to-date facts when needed.

    Many 10-Qs tag only six- and nine-month totals; the quarter is the
    difference between consecutive year-to-date values sharing a start date.
    """
    periods = _latest_revision(_facts(payload, tag, unit))
    out: dict[date, Fact] = {}
    for (start, end), fact in periods.items():
        if start is not None and _is_quarter(start, end):
            out[end] = fact
    for (start, end), (val, filed, accn) in periods.items():
        if start is None or end in out:
            continue
        for (prior_start, prior_end), (prior_val, _, _) in periods.items():
            if prior_start == start and _is_quarter(prior_end, end):
                out[end] = (val - prior_val, filed, accn)
                break
    return out


def extract_nav(payload: dict[str, Any], ticker: str, cik: int) -> dict[str, object] | None:
    rows = [
        (end, filed, nav, tag, accn)
        for tag in TAG_CANDIDATES
        for end, (nav, filed, accn) in instant_series(payload, tag, "USD/shares").items()
        if nav > 0
    ]
    if not rows:
        return None
    end, filed, nav, tag, accession = max(rows, key=lambda x: (x[0], x[1]))
    return {
        "date": end.isoformat(), "ticker": ticker, "cik": cik,
        "nav_per_share": nav, "source_url": _filing_url(cik, accession),
        "filed_at": filed.isoformat(), "tag": tag,
    }


def extract_metrics(payload: dict[str, Any], ticker: str, cik: int) -> list[dict[str, object]]:
    """One row per recent quarter end with NAV, NAV QoQ % and PIK share of income."""
    nav = {
        end: fact
        for end, fact in instant_series(payload, TAG_CANDIDATES[0], "USD/shares").items()
        if fact[0] > 0
    }
    pik = quarterly_series(payload, PIK_TAG)
    income = quarterly_series(payload, INCOME_TAG)
    rows: list[dict[str, object]] = []
    for end in sorted(nav)[-QUARTERS_KEPT:]:
        value, _, accn = nav[end]
        prior = next((nav[d][0] for d in nav if d < end and _is_quarter(d, end)), None)
        notes = ["SEC XBRL; non_accrual_pct has no us-gaap tag"]
        pik_pct: object = ""
        if end in pik and end in income and income[end][0] > 0:
            pik_pct = round(100 * pik[end][0] / income[end][0], 2)
        else:
            missing = PIK_TAG if end not in pik else INCOME_TAG
            notes.append(f"pik_income_pct unavailable: {missing} not tagged")
        rows.append({
            "date": end.isoformat(), "ticker": ticker, "manager": MANAGERS.get(ticker, ""),
            "non_accrual_pct": "", "pik_income_pct": pik_pct,
            "nav_per_share": value,
            "nav_qoq_pct": "" if prior is None else round(100 * (value / prior - 1), 2),
            "realized_loss_pct": "", "interest_coverage": "",
            "source_url": _filing_url(cik, accn), "notes": "; ".join(notes),
        })
    return rows


def merge_manual(sec_rows: list[dict[str, object]], path: Path) -> list[dict[str, object]]:
    """Fill gaps in SEC rows with hand-entered values, keeping manual-only rows.

    SEC values win where present so reruns pick up restatements; hand entries
    supply fields XBRL lacks (non-accruals, realized losses, coverage).
    """
    if not path.exists():
        return list(sec_rows)
    with path.open(newline="") as file:
        manual = {(r["date"], r["ticker"]): r for r in csv.DictReader(file)}
    merged: list[dict[str, object]] = []
    for row in sec_rows:
        hand = manual.pop((str(row["date"]), str(row["ticker"])), {})
        merged.append({k: hand.get(k, "") if v == "" else v for k, v in row.items()})
    merged.extend({k: r.get(k, "") for k in METRIC_HEADERS} for r in manual.values())
    return merged


def main() -> None:
    agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not agent:
        raise SystemExit("Set SEC_USER_AGENT to a descriptive name and contact email per SEC guidance")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    observations = []
    metrics: list[dict[str, object]] = []
    coverage = []
    for ticker, cik in ISSUERS.items():
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
        try:
            request = Request(url, headers={"User-Agent": agent, "Accept": "application/json"})
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
            result = extract_nav(payload, ticker, cik)
            if result:
                observations.append(result)
            issuer_metrics = extract_metrics(payload, ticker, cik)
            metrics.extend(issuer_metrics)
            has_pik = bool(issuer_metrics) and issuer_metrics[-1]["pik_income_pct"] != ""
            coverage.append({
                "ticker": ticker,
                "status": "NAV FOUND" if result else "NO COMPARABLE NAV TAG",
                "pik": "PIK FOUND" if has_pik else "NO COMPARABLE PIK TAG",
                "error": "",
            })
        except Exception as exc:
            coverage.append({"ticker": ticker, "status": "FETCH FAILED", "pik": "", "error": str(exc)[:250]})
    with OUT.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(observations)
    merged = sorted(merge_manual(metrics, METRICS_OUT), key=lambda r: (str(r["ticker"]), str(r["date"])))
    with METRICS_OUT.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=METRIC_HEADERS)
        writer.writeheader()
        writer.writerows(merged)
    with OUT.with_name("bdc_sec_coverage.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=("ticker", "status", "pik", "error"))
        writer.writeheader()
        writer.writerows(coverage)
    print(json.dumps({"nav_observations": len(observations), "metric_rows": len(merged), "coverage": coverage}, indent=2))


if __name__ == "__main__":
    main()
