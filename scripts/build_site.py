"""Build the GitHub Pages site for the stress dashboard report.

The latest report is served at the site root (a permanent link). Each run also
keeps a dated copy under reports/, and copies older than the retention window
are deleted. The workflow publishes the result as a single orphan commit, so
deleted reports do not linger in the branch history.
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

STAMP_FORMAT = "%Y-%m-%dT%H%MZ"
STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{4}Z)\.html$")


def report_time(path: Path) -> datetime | None:
    match = STAMP_RE.match(path.name)
    if not match:
        return None
    return datetime.strptime(match.group(1), STAMP_FORMAT).replace(tzinfo=timezone.utc)


def build_site(report: Path, previous: Path | None, site: Path, now: datetime, retention_days: int) -> list[Path]:
    """Write the site to `site` and return the dated reports kept, newest first."""
    reports = site / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    if previous is not None and (previous / "reports").is_dir():
        for old in (previous / "reports").glob("*.html"):
            if report_time(old):
                shutil.copy2(old, reports / old.name)
    shutil.copy2(report, reports / f"{now.strftime(STAMP_FORMAT)}.html")

    cutoff = now - timedelta(days=retention_days)
    kept = []
    for path in reports.glob("*.html"):
        when = report_time(path)
        if when is None or when < cutoff:
            path.unlink()
        else:
            kept.append(path)
    kept.sort(key=lambda p: p.name, reverse=True)

    shutil.copy2(report, site / "index.html")
    items = "\n".join(
        f'<li><a href="reports/{html.escape(p.name)}">{html.escape(p.stem)}</a></li>' for p in kept
    )
    (site / "archive.html").write_text(
        "<!doctype html><meta charset=\"utf-8\"><title>Stress dashboard reports</title>"
        "<h1>Stress dashboard reports</h1>"
        f"<p><a href=\"./\">Latest report</a>. Dated copies are kept for {retention_days} days.</p>"
        f"<ul>\n{items}\n</ul>\n",
        encoding="utf-8",
    )
    (site / ".nojekyll").write_text("")
    return kept


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("site", type=Path)
    parser.add_argument("--previous", type=Path, help="checkout of the currently published site")
    parser.add_argument("--retention-days", type=int, default=14)
    args = parser.parse_args()
    kept = build_site(args.report, args.previous, args.site, datetime.now(timezone.utc), args.retention_days)
    print(f"Site built with {len(kept)} dated report(s): {', '.join(p.stem for p in kept)}")


if __name__ == "__main__":
    main()
