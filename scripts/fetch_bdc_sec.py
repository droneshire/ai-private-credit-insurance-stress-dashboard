"""Fetch auditable SEC XBRL NAV observations for selected BDCs.

SEC companyfacts often lacks comparable PIK/non-accrual concepts; those remain
unavailable rather than inferred from unrelated GAAP tags.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime
from pathlib import Path
from urllib.request import Request, urlopen

ISSUERS = {"ARCC": 1287750, "MAIN": 1396440, "OBDC": 1655888, "FSK": 1422183}
OUT = Path("stress_dashboard_data/bdc_sec_nav.csv")
TAG_CANDIDATES = ("NetAssetValuePerShare",)
HEADERS = ("date", "ticker", "cik", "nav_per_share", "source_url", "filed_at", "tag")

def extract_nav(payload: dict[str, object], ticker: str, cik: int) -> dict[str, object] | None:
    facts = payload.get("facts", {}).get("us-gaap", {})
    rows = []
    for tag in TAG_CANDIDATES:
        for unit, values in facts.get(tag, {}).get("units", {}).items():
            if unit != "USD/shares":
                continue
            for value in values:
                if value.get("form") not in ("10-Q", "10-K"):
                    continue
                if not all(k in value for k in ("end", "filed", "val", "accn")):
                    continue
                try:
                    end = date.fromisoformat(value["end"])
                    filed = date.fromisoformat(value["filed"])
                    nav = float(value["val"])
                except (ValueError, TypeError):
                    continue
                if nav <= 0 or end > date.today() or filed > date.today():
                    continue
                rows.append((end, filed, nav, tag, value["accn"]))
    if not rows:
        return None
    # Prefer latest reporting period, then latest filed revision.
    end, filed, nav, tag, accession = max(rows, key=lambda x: (x[0], x[1]))
    return {
        "date": end.isoformat(), "ticker": ticker, "cik": cik,
        "nav_per_share": nav,
        "source_url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/",
        "filed_at": filed.isoformat(), "tag": tag,
    }

def main() -> None:
    agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not agent:
        raise SystemExit("Set SEC_USER_AGENT to a descriptive name and contact email per SEC guidance")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    observations = []
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
            coverage.append({"ticker": ticker, "status": "NAV FOUND" if result else "NO COMPARABLE NAV TAG", "error": ""})
        except Exception as exc:
            coverage.append({"ticker": ticker, "status": "FETCH FAILED", "error": str(exc)[:250]})
    with OUT.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(observations)
    with OUT.with_name("bdc_sec_coverage.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=("ticker", "status", "error"))
        writer.writeheader()
        writer.writerows(coverage)
    print(json.dumps({"nav_observations": len(observations), "coverage": coverage}, indent=2))

if __name__ == "__main__":
    main()
