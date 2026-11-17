"""Collect compute-economics indicators.

- SEC XBRL: trailing-twelve-month capital expenditure for the largest AI
  infrastructure spenders, and TTM revenue for NVIDIA and CoreWeave. TTM sums
  four derived quarters, so differing fiscal calendars compare cleanly.
- Vast.ai public marketplace: median on-demand price per GPU-hour, appended to
  gpu_price_history.csv so a trend accumulates across runs.

The SEC identity (SEC_USER_AGENT) is sent to SEC hosts only; Vast.ai receives a
generic application name.
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from sec_client import SecClient, filing_folder, sec_user_agent
from xbrl_facts import first_quarterly_series, trailing_sum

DATA_DIR = Path("stress_dashboard_data")
SEC_OUT = DATA_DIR / "compute_sec.csv"
GPU_OUT = DATA_DIR / "gpu_price_history.csv"
CAPEX_TAGS = ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets")
REVENUE_TAGS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")
# company -> (CIK, metrics, group)
COMPANIES: dict[str, tuple[int, tuple[str, ...], str]] = {
    "Microsoft": (789019, ("capex",), "hyperscaler"),
    "Alphabet": (1652044, ("capex",), "hyperscaler"),
    "Amazon": (1018724, ("capex",), "hyperscaler"),
    "Meta": (1326801, ("capex",), "hyperscaler"),
    "Oracle": (1341439, ("capex",), "hyperscaler"),
    "NVIDIA": (1045810, ("revenue",), "supplier"),
    "CoreWeave": (1769628, ("capex", "revenue"), "neocloud"),
}
SEC_HEADERS = ("date", "company", "group", "metric", "quarter_usd_m", "ttm_usd_m", "ttm_yoy_pct", "tag", "source_url")
GPU_HEADERS = ("observed_at", "gpu", "offers", "median_usd_per_gpu_hour", "p25_usd_per_gpu_hour", "source")
GPUS = ("H100 SXM", "H200", "B200", "A100 SXM4")
VAST_URL = "https://console.vast.ai/api/v0/bundles/?q="
GENERIC_AGENT = "AIPrivateCreditDashboard/1.0"
QUARTERS_KEPT = 5


def metric_rows(payload: dict[str, Any], company: str, cik: int, metric: str, group: str) -> list[dict[str, object]]:
    quarters = first_quarterly_series(payload, CAPEX_TAGS if metric == "capex" else REVENUE_TAGS)
    rows: list[dict[str, object]] = []
    ends = sorted(quarters)
    for i, end in enumerate(ends):
        ttm = trailing_sum(quarters, end)
        prior = ends[i - 4] if i >= 4 else None
        year_ago = trailing_sum(quarters, prior) if prior and 350 <= (end - prior).days <= 380 else None
        (value, _, accn), tag = quarters[end]
        rows.append({
            "date": end.isoformat(), "company": company, "group": group, "metric": metric,
            "quarter_usd_m": round(value / 1e6, 1),
            "ttm_usd_m": "" if ttm is None else round(ttm / 1e6, 1),
            "ttm_yoy_pct": "" if ttm is None or not year_ago else round(100 * (ttm / year_ago - 1), 2),
            "tag": tag, "source_url": filing_folder(cik, accn),
        })
    return rows[-QUARTERS_KEPT:]


def gpu_snapshot(gpu: str, observed_at: str) -> dict[str, object] | None:
    query = json.dumps({"gpu_name": {"eq": gpu}, "rentable": {"eq": True}, "type": "on-demand", "limit": 500})
    request = Request(VAST_URL + quote(query), headers={"User-Agent": GENERIC_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=60) as response:
        offers = json.load(response).get("offers", [])
    prices = sorted(o["dph_total"] / o["num_gpus"] for o in offers if o.get("num_gpus") and o.get("dph_total"))
    if not prices:
        return None
    return {
        "observed_at": observed_at, "gpu": gpu, "offers": len(prices),
        "median_usd_per_gpu_hour": round(statistics.median(prices), 4),
        "p25_usd_per_gpu_hour": round(statistics.quantiles(prices, n=4)[0], 4) if len(prices) > 1 else prices[0],
        "source": "Vast.ai public marketplace, on-demand offers",
    }


def write_csv(path: Path, headers: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    coverage: list[dict[str, object]] = []
    sec_rows: list[dict[str, object]] = []
    client = SecClient(sec_user_agent())
    for company, (cik, metrics, group) in COMPANIES.items():
        try:
            payload = client.companyfacts(cik)
            for metric in metrics:
                rows = metric_rows(payload, company, cik, metric, group)
                sec_rows.extend(rows)
                coverage.append({"source": company, "metric": metric, "rows": len(rows), "error": ""})
        except Exception as exc:
            coverage.append({"source": company, "metric": ",".join(metrics), "rows": 0, "error": str(exc)[:250]})
    write_csv(SEC_OUT, SEC_HEADERS, sec_rows)

    history: list[dict[str, object]] = []
    if GPU_OUT.exists():
        with GPU_OUT.open(newline="") as file:
            history = [dict(r) for r in csv.DictReader(file)]
    observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    for gpu in GPUS:
        try:
            snap = gpu_snapshot(gpu, observed_at)
            if snap:
                history.append(snap)
            coverage.append({"source": "Vast.ai", "metric": gpu, "rows": int(bool(snap)), "error": ""})
        except Exception as exc:
            coverage.append({"source": "Vast.ai", "metric": gpu, "rows": 0, "error": str(exc)[:250]})
    write_csv(GPU_OUT, GPU_HEADERS, history)
    write_csv(DATA_DIR / "compute_coverage.csv", ("source", "metric", "rows", "error"), coverage)
    print(json.dumps({"sec_rows": len(sec_rows), "gpu_history_rows": len(history), "coverage": coverage}, indent=2))
    if not sec_rows:
        sys.exit("No compute data collected")


if __name__ == "__main__":
    main()
