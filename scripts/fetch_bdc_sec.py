"""Fetch auditable SEC observations for selected BDCs.

From XBRL companyfacts (standard us-gaap tags only): NAV per share, NAV
quarter-over-quarter change, PIK interest share of investment income, realized
losses as a share of the portfolio, and interest coverage. Non-accrual rates
have no us-gaap concept, so they are parsed from the earnings release (8-K
item 2.02, exhibit 99) with the quoted sentence kept as evidence. Values
entered by hand in bdc_metrics.csv fill the fields SEC leaves blank.
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

from release_text import NonAccrual, parse_non_accrual, quarter_end_before
from sec_client import SecClient, filing_folder, html_to_text, sec_user_agent
from xbrl_facts import first_quarterly_series, instant_series, is_quarter, quarterly_series

ISSUERS = {"ARCC": 1287750, "MAIN": 1396440, "OBDC": 1655888, "FSK": 1422183}
MANAGERS = {"ARCC": "Ares", "MAIN": "Main Street (internal)", "OBDC": "Blue Owl", "FSK": "FS/KKR"}
OUT = Path("stress_dashboard_data/bdc_sec_nav.csv")
METRICS_OUT = OUT.with_name("bdc_metrics.csv")
EVIDENCE_OUT = OUT.with_name("bdc_non_accrual_evidence.csv")
TAG_CANDIDATES = ("NetAssetValuePerShare",)
PIK_TAG = "InterestIncomeOperatingPaidInKind"
INCOME_TAG = "GrossInvestmentIncomeOperating"
NII_TAG = "NetInvestmentIncome"
PORTFOLIO_TAG = "InvestmentOwnedAtFairValue"
# Priority order: the first tag an issuer reports for a quarter is used.
REALIZED_TAGS = (
    "RealizedInvestmentGainsLosses",
    "DebtAndEquitySecuritiesRealizedGainLoss",
    "RealizedGainLossInvestmentDerivativeAndForeignCurrencyTransactionPriceChangeOperatingBeforeTax",
    "RealizedGainLossInvestmentDerivativeAndForeignCurrencyTransactionPriceChangeOperatingAfterTax",
)
INTEREST_TAGS = ("InterestExpense", "InterestExpenseOperating", "InterestExpenseDebt")
HEADERS = ("date", "ticker", "cik", "nav_per_share", "source_url", "filed_at", "tag")
METRIC_HEADERS = (
    "date", "ticker", "manager", "non_accrual_pct", "non_accrual_fv_pct", "pik_income_pct",
    "nav_per_share", "nav_qoq_pct", "realized_loss_pct", "interest_coverage",
    "source_url", "notes",
)
EVIDENCE_HEADERS = ("date", "ticker", "non_accrual_pct", "non_accrual_fv_pct", "source_url", "quote")
QUARTERS_KEPT = 4


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
        "nav_per_share": nav, "source_url": filing_folder(cik, accession),
        "filed_at": filed.isoformat(), "tag": tag,
    }


def extract_metrics(
    payload: dict[str, Any],
    ticker: str,
    cik: int,
    non_accruals: dict[date, tuple[NonAccrual, str]] | None = None,
) -> list[dict[str, object]]:
    """One row per recent quarter end from XBRL facts plus parsed non-accruals."""
    non_accruals = non_accruals or {}
    nav = {end: f for end, f in instant_series(payload, TAG_CANDIDATES[0], "USD/shares").items() if f[0] > 0}
    portfolio = instant_series(payload, PORTFOLIO_TAG)
    pik = quarterly_series(payload, PIK_TAG)
    income = quarterly_series(payload, INCOME_TAG)
    nii = quarterly_series(payload, NII_TAG)
    realized = first_quarterly_series(payload, REALIZED_TAGS)
    interest = first_quarterly_series(payload, INTEREST_TAGS)
    rows: list[dict[str, object]] = []
    for end in sorted(nav)[-QUARTERS_KEPT:]:
        value, _, accn = nav[end]
        prior = next((nav[d][0] for d in nav if d < end and is_quarter(d, end)), None)
        notes = ["SEC XBRL"]
        row: dict[str, object] = {
            "date": end.isoformat(), "ticker": ticker, "manager": MANAGERS.get(ticker, ""),
            "non_accrual_pct": "", "non_accrual_fv_pct": "", "pik_income_pct": "",
            "nav_per_share": value,
            "nav_qoq_pct": "" if prior is None else round(100 * (value / prior - 1), 2),
            "realized_loss_pct": "", "interest_coverage": "",
            "source_url": filing_folder(cik, accn),
        }
        if end in pik and end in income and income[end][0] > 0:
            row["pik_income_pct"] = round(100 * pik[end][0] / income[end][0], 2)
        else:
            notes.append(f"pik_income_pct: {PIK_TAG if end not in pik else INCOME_TAG} not tagged")
        if end in realized and end in portfolio and portfolio[end][0] > 0:
            (gain, _, _), tag = realized[end]
            # Positive = net realized loss, as % of the portfolio at fair value for the quarter.
            row["realized_loss_pct"] = round(-100 * gain / portfolio[end][0], 3)
            notes.append(f"realized: {tag}")
        else:
            notes.append("realized_loss_pct: no realized gain/loss tag")
        if end in interest and end in nii and interest[end][0][0] > 0:
            (expense, _, _), tag = interest[end]
            row["interest_coverage"] = round((nii[end][0] + expense) / expense, 2)
            notes.append(f"coverage: ({NII_TAG} + {tag}) / {tag}")
        else:
            notes.append("interest_coverage: interest expense not tagged")
        if end in non_accruals:
            found, url = non_accruals[end]
            row["non_accrual_pct"] = found.at_cost_pct
            row["non_accrual_fv_pct"] = found.at_fair_value_pct
            notes.append(f"non-accrual: earnings release {url}")
        else:
            notes.append("non_accrual_pct: not found in earnings release")
        row["notes"] = "; ".join(notes)
        rows.append(row)
    return rows


def fetch_non_accruals(client: SecClient, cik: int, limit: int = 10) -> dict[date, tuple[NonAccrual, str]]:
    """Non-accruals from recent earnings releases, keyed by the quarter each reports.

    A later release for the same quarter (e.g. final after preliminary) wins.
    """
    found: dict[date, tuple[NonAccrual, str]] = {}
    for filing in sorted(client.filings(cik, ("8-K",), item="2.02")[:limit], key=lambda f: f["filed"]):
        for doc_type, url in client.documents(cik, filing["accession"]):
            if not doc_type.startswith("EX-99") or not url.lower().endswith((".htm", ".html", ".txt")):
                continue
            parsed = parse_non_accrual(html_to_text(client.get_text(url)))
            if parsed:
                found[quarter_end_before(filing["filed"])] = (parsed, url)
                break
    return found


def merge_manual(sec_rows: list[dict[str, object]], path: Path) -> list[dict[str, object]]:
    """Fill gaps in SEC rows with hand-entered values, keeping manual-only rows.

    SEC values win where present so reruns pick up restatements; hand entries
    supply fields SEC lacks.
    """
    if not path.exists():
        return list(sec_rows)
    with path.open(newline="") as file:
        manual = {(r["date"], r["ticker"]): r for r in csv.DictReader(file)}
    merged: list[dict[str, object]] = []
    for row in sec_rows:
        hand = manual.pop((str(row["date"]), str(row["ticker"])), {})
        merged.append({k: hand.get(k) or "" if v == "" else v for k, v in row.items()})
    merged.extend({k: r.get(k) or "" for k in METRIC_HEADERS} for r in manual.values())
    return merged


def write_csv(path: Path, headers: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    client = SecClient(sec_user_agent())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    observations = []
    metrics: list[dict[str, object]] = []
    evidence: list[dict[str, object]] = []
    coverage: list[dict[str, object]] = []
    for ticker, cik in ISSUERS.items():
        status: dict[str, object] = {"ticker": ticker, "status": "", "pik": "", "non_accrual": "", "error": ""}
        try:
            payload = client.companyfacts(cik)
            result = extract_nav(payload, ticker, cik)
            if result:
                observations.append(result)
            try:
                non_accruals = fetch_non_accruals(client, cik)
            except Exception as exc:
                non_accruals = {}
                status["error"] = f"earnings releases: {str(exc)[:200]}"
            issuer_metrics = extract_metrics(payload, ticker, cik, non_accruals)
            metrics.extend(issuer_metrics)
            evidence.extend(
                {"date": end.isoformat(), "ticker": ticker, "non_accrual_pct": na.at_cost_pct,
                 "non_accrual_fv_pct": na.at_fair_value_pct, "source_url": url, "quote": na.quote}
                for end, (na, url) in sorted(non_accruals.items())
            )
            latest = issuer_metrics[-1] if issuer_metrics else {}
            status["status"] = "NAV FOUND" if result else "NO COMPARABLE NAV TAG"
            status["pik"] = "PIK FOUND" if latest.get("pik_income_pct", "") != "" else "NO COMPARABLE PIK TAG"
            status["non_accrual"] = (
                "NON-ACCRUAL FOUND" if latest.get("non_accrual_pct", "") != "" else "NOT FOUND IN RELEASE"
            )
        except Exception as exc:
            status.update(status="FETCH FAILED", error=str(exc)[:250])
        coverage.append(status)
    write_csv(OUT, HEADERS, observations)
    merged = sorted(merge_manual(metrics, METRICS_OUT), key=lambda r: (str(r["ticker"]), str(r["date"])))
    write_csv(METRICS_OUT, METRIC_HEADERS, merged)
    write_csv(EVIDENCE_OUT, EVIDENCE_HEADERS, evidence)
    write_csv(OUT.with_name("bdc_sec_coverage.csv"), ("ticker", "status", "pik", "non_accrual", "error"), coverage)
    print(json.dumps({"nav_observations": len(observations), "metric_rows": len(merged), "coverage": coverage}, indent=2))


if __name__ == "__main__":
    main()
