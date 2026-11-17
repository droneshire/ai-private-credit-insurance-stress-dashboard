"""Minimal SEC EDGAR client honouring the fair-access policy.

Every request carries the SEC_USER_AGENT identity and is spaced to stay well
below SEC's limit of 10 requests per second. The identity is sent to SEC hosts
only.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MIN_INTERVAL_S = 0.15
RETRIES = 3
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


def sec_user_agent() -> str:
    agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not agent:
        raise SystemExit("Set SEC_USER_AGENT to a descriptive name and contact email per SEC guidance")
    return agent


def filing_folder(cik: int, accession: str) -> str:
    return f"{ARCHIVES}/{cik}/{accession.replace('-', '')}/"


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()


class SecClient:
    def __init__(self, agent: str, timeout: float = 60) -> None:
        self.agent = agent
        self.timeout = timeout
        self._last = 0.0

    def get_bytes(self, url: str) -> bytes:
        if not url.startswith(("https://www.sec.gov/", "https://data.sec.gov/")):
            raise ValueError(f"SecClient only fetches SEC hosts: {url}")
        for attempt in range(RETRIES):
            wait = self._last + MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                request = Request(url, headers={"User-Agent": self.agent, "Accept-Encoding": "identity"})
                with urlopen(request, timeout=self.timeout) as response:
                    body: bytes = response.read()
                    return body
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == RETRIES - 1:
                    raise
            except URLError:
                if attempt == RETRIES - 1:
                    raise
            time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")

    def get_json(self, url: str) -> dict[str, Any]:
        payload: dict[str, Any] = json.loads(self.get_bytes(url))
        return payload

    def get_text(self, url: str) -> str:
        return self.get_bytes(url).decode("utf-8", errors="replace")

    def companyfacts(self, cik: int) -> dict[str, Any]:
        return self.get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")

    def filings(self, cik: int, forms: tuple[str, ...], item: str | None = None) -> list[dict[str, Any]]:
        """Recent filings of the given forms, newest first."""
        recent = self.get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")["filings"]["recent"]
        out = []
        for i, form in enumerate(recent["form"]):
            if form not in forms or (item and item not in recent["items"][i].split(",")):
                continue
            out.append({
                "form": form,
                "accession": recent["accessionNumber"][i],
                "filed": date.fromisoformat(recent["filingDate"][i]),
                "report_date": recent["reportDate"][i],
                "primary": recent["primaryDocument"][i],
            })
        return out

    def documents(self, cik: int, accession: str) -> list[tuple[str, str]]:
        """(document type, absolute URL) pairs from a filing's index page."""
        index = self.get_text(f"{filing_folder(cik, accession)}{accession}-index.html")
        return parse_filing_index(index)


def parse_filing_index(index_html: str) -> list[tuple[str, str]]:
    docs = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", index_html, re.S | re.I):
        href = re.search(r'href="([^"]+)"', row)
        if not href:
            continue
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
        doc_type = cells[3] if len(cells) > 3 else ""
        path = href.group(1).replace("/ix?doc=", "")
        docs.append((doc_type, f"https://www.sec.gov{path}" if path.startswith("/") else path))
    return docs
