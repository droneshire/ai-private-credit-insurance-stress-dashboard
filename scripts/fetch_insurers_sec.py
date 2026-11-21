"""Fetch GAAP balance-sheet proxies for private-credit-heavy life insurers from SEC.

NAIC statutory filings are not freely available, so this writes a clearly
labelled GAAP proxy (insurer_sec_gaap.csv) with the insurer_statutory.csv
columns:

- capital_surplus_usd_m: GAAP stockholders' equity (not statutory surplus)
- level3_assets_usd_m: recurring Level 3 fair-value assets, read from the
  latest 10-Q/10-K XBRL instance (dimensional, so absent from companyfacts)
- unrealized_losses_usd_m: gross unrealized losses on AFS debt securities
- rbc_ratio_pct: stated in the earnings release, with the quote kept
- aoci_usd_m (extra column): accumulated other comprehensive income, so equity
  can be compared excluding mark-to-market moves already captured by
  unrealized losses

Rating migration (NAIC-1 to lower) is not disclosed in GAAP filings and is
left blank. Statutory rows in insurer_statutory.csv take precedence in the
notebook.
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

from release_text import Rbc, parse_rbc, quarter_end_before
from sec_client import SecClient, filing_folder, html_to_text, sec_user_agent
from xbrl_facts import first_instant_series, instance_dimensional_value

INSURERS: dict[str, tuple[int, str]] = {
    "Athene Holding": (1527469, "Apollo"),
    "Corebridge Financial": (1889539, "Corebridge"),
    "F&G Annuities & Life": (1934850, "FNF"),
    "Jackson Financial": (1822993, "Jackson"),
    "Lincoln National": (59558, "Lincoln"),
    "Brighthouse Financial": (1685040, "Brighthouse"),
}
OUT = Path("stress_dashboard_data/insurer_sec_gaap.csv")
EVIDENCE_OUT = OUT.with_name("insurer_rbc_evidence.csv")
EQUITY_TAGS = ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")
ASSET_TAGS = ("Assets",)
UNREALIZED_TAGS = (
    "DebtSecuritiesAvailableForSaleUnrealizedLossPositionAccumulatedLoss",
    "AvailableForSaleSecuritiesGrossUnrealizedLoss",
)
AOCI_TAGS = ("AccumulatedOtherComprehensiveIncomeLossNetOfTax",)
REINSURANCE_TAGS = ("ReinsuranceRecoverables", "ReinsuranceRecoverablesIncludingReinsurancePremiumPaid")
LEVEL3 = {"FairValueByFairValueHierarchyLevelAxis": "FairValueInputsLevel3Member"}
RECURRING = {"FairValueByMeasurementFrequencyAxis": "FairValueMeasurementsRecurringMember"}
COLUMNS = (
    "date", "insurer", "parent",
    "total_assets_usd_m", "capital_surplus_usd_m", "rbc_ratio_pct",
    "level3_assets_usd_m", "schedule_ba_usd_m",
    "clo_abs_usd_m", "unrealized_losses_usd_m",
    "reinsurance_recoverables_usd_m", "ceded_reserves_usd_m",
    "total_reserves_usd_m", "affiliated_investments_usd_m",
    "naic1_to_lower_usd_m", "aoci_usd_m", "source_url", "notes",
)
QUARTERS_KEPT = 5  # five period ends give a year-over-year comparison


def _usd_m(value: float) -> float:
    return round(value / 1e6, 1)


def extract_rows(payload: dict[str, Any], insurer: str, parent: str, cik: int) -> list[dict[str, object]]:
    """One row per recent period end from non-dimensional companyfacts."""
    equity = first_instant_series(payload, EQUITY_TAGS)
    assets = first_instant_series(payload, ASSET_TAGS)
    unrealized = first_instant_series(payload, UNREALIZED_TAGS)
    reinsurance = first_instant_series(payload, REINSURANCE_TAGS)
    aoci = first_instant_series(payload, AOCI_TAGS)
    rows: list[dict[str, object]] = []
    for end in sorted(d for d in equity if d in assets)[-QUARTERS_KEPT:]:
        (eq, _, accn), eq_tag = equity[end]
        row: dict[str, object] = {c: "" for c in COLUMNS}
        notes = [f"GAAP proxy from SEC XBRL; capital = {eq_tag} (not statutory surplus)"]
        row.update(
            date=end.isoformat(), insurer=insurer, parent=parent,
            total_assets_usd_m=_usd_m(assets[end][0][0]), capital_surplus_usd_m=_usd_m(eq),
            source_url=filing_folder(cik, accn),
        )
        if end in unrealized:
            row["unrealized_losses_usd_m"] = _usd_m(abs(unrealized[end][0][0]))
        else:
            notes.append("unrealized losses not tagged")
        if end in aoci:
            row["aoci_usd_m"] = _usd_m(aoci[end][0][0])
        if end in reinsurance:
            row["reinsurance_recoverables_usd_m"] = _usd_m(reinsurance[end][0][0])
        row["notes"] = "; ".join(notes)
        rows.append(row)
    return rows


def latest_level3(client: SecClient, cik: int) -> tuple[date, float, str] | None:
    """Recurring Level 3 assets from the latest periodic report's XBRL instance."""
    for filing in client.filings(cik, ("10-Q", "10-K"))[:1]:
        period_end = filing["report_date"]
        for _, url in client.documents(cik, filing["accession"]):
            if not url.endswith("_htm.xml"):
                continue
            instance = client.get_bytes(url)
            value = instance_dimensional_value(instance, "AssetsFairValueDisclosure", period_end, LEVEL3, RECURRING)
            if value is not None:
                return date.fromisoformat(period_end), value, url
    return None


def latest_rbc(client: SecClient, cik: int, limit: int = 2) -> dict[date, tuple[Rbc, str]]:
    """RBC ratios stated in recent earnings releases, keyed by the quarter reported."""
    found: dict[date, tuple[Rbc, str]] = {}
    for filing in sorted(client.filings(cik, ("8-K",), item="2.02")[:limit], key=lambda f: f["filed"]):
        for doc_type, url in client.documents(cik, filing["accession"]):
            if not doc_type.startswith("EX-99") or not url.lower().endswith((".htm", ".html")):
                continue
            parsed = parse_rbc(html_to_text(client.get_text(url)))
            if parsed:
                found[quarter_end_before(filing["filed"])] = (parsed, url)
                break
    return found


def main() -> None:
    client = SecClient(sec_user_agent())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    evidence: list[dict[str, object]] = []
    coverage = []
    for insurer, (cik, parent) in INSURERS.items():
        status: dict[str, object] = {"insurer": insurer, "rows": 0, "level3": "", "rbc": "", "error": ""}
        try:
            insurer_rows = extract_rows(client.companyfacts(cik), insurer, parent, cik)
            by_date = {str(r["date"]): r for r in insurer_rows}
            level3 = latest_level3(client, cik)
            if level3 and level3[0].isoformat() in by_date:
                row = by_date[level3[0].isoformat()]
                row["level3_assets_usd_m"] = _usd_m(level3[1])
                row["notes"] = f"{row['notes']}; level 3: recurring AssetsFairValueDisclosure in {level3[2]}"
            status["level3"] = "FOUND" if level3 else "NOT TAGGED"
            rbc = latest_rbc(client, cik)
            for end, (found, url) in rbc.items():
                if end.isoformat() in by_date:
                    row = by_date[end.isoformat()]
                    row["rbc_ratio_pct"] = found.ratio_pct
                    row["notes"] = f"{row['notes']}; RBC ({found.kind}) from {url}"
                evidence.append({"date": end.isoformat(), "insurer": insurer, "rbc_ratio_pct": found.ratio_pct,
                                 "kind": found.kind, "source_url": url, "quote": found.quote})
            status["rbc"] = "FOUND" if rbc else "NOT STATED IN RELEASE"
            status["rows"] = len(insurer_rows)
            rows.extend(insurer_rows)
        except Exception as exc:
            status["error"] = str(exc)[:250]
        coverage.append(status)
    for path, headers, data in (
        (OUT, COLUMNS, rows),
        (EVIDENCE_OUT, ("date", "insurer", "rbc_ratio_pct", "kind", "source_url", "quote"), evidence),
        (OUT.with_name("insurer_sec_coverage.csv"), ("insurer", "rows", "level3", "rbc", "error"), coverage),
    ):
        with path.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=headers)
            writer.writeheader()
            writer.writerows(data)
    print(json.dumps({"rows": len(rows), "coverage": coverage}, indent=2))


if __name__ == "__main__":
    main()
