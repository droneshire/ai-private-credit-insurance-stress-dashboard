import csv
from pathlib import Path

import pytest

from fetch_bdc_sec import METRIC_HEADERS, merge_manual
from sec_client import SecClient, parse_filing_index


def test_merge_manual_fills_gaps_but_sec_values_win(tmp_path: Path) -> None:
    path = tmp_path / "bdc_metrics.csv"
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["date", "ticker", "non_accrual_pct", "nav_per_share", "notes"])
        writer.writeheader()
        writer.writerow({"date": "2026-09-30", "ticker": "MAIN", "non_accrual_pct": "1.5", "nav_per_share": "1",
                         "notes": "hand"})
        writer.writerow({"date": "2026-09-30", "ticker": "HAND", "non_accrual_pct": "2", "nav_per_share": "",
                         "notes": ""})
    sec = [{k: "" for k in METRIC_HEADERS} | {"date": "2026-09-30", "ticker": "MAIN", "nav_per_share": 34.22,
                                               "notes": "SEC XBRL"}]
    merged = {r["ticker"]: r for r in merge_manual(sec, path)}
    assert merged["MAIN"]["non_accrual_pct"] == "1.5"
    assert merged["MAIN"]["nav_per_share"] == 34.22
    assert merged["HAND"]["non_accrual_pct"] == "2"
    assert set(merged["HAND"]) == set(METRIC_HEADERS)


def test_parse_filing_index_reads_document_types() -> None:
    html = """<table><tr><th>Seq</th></tr>
    <tr><td>1</td><td>8-K</td><td><a href="/ix?doc=/Archives/edgar/data/1/0001/main.htm">main.htm</a></td><td>8-K</td></tr>
    <tr><td>2</td><td>PRESS RELEASE</td><td><a href="/Archives/edgar/data/1/0001/pr.htm">pr.htm</a></td><td>EX-99.1</td></tr>
    </table>"""
    assert parse_filing_index(html) == [
        ("8-K", "https://www.sec.gov/Archives/edgar/data/1/0001/main.htm"),
        ("EX-99.1", "https://www.sec.gov/Archives/edgar/data/1/0001/pr.htm"),
    ]


def test_sec_client_refuses_non_sec_hosts() -> None:
    with pytest.raises(ValueError):
        SecClient("test agent").get_bytes("https://example.com/")
