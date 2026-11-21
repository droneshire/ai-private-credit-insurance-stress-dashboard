from datetime import datetime, timezone
from pathlib import Path

from build_site import build_site


def test_build_site_publishes_latest_and_prunes_old_reports(tmp_path: Path) -> None:
    report = tmp_path / "report.html"
    report.write_text("new")
    previous = tmp_path / "previous"
    (previous / "reports").mkdir(parents=True)
    (previous / "reports" / "2026-11-10T0900Z.html").write_text("recent")
    (previous / "reports" / "2026-11-01T0900Z.html").write_text("expired")
    (previous / "reports" / "notes.html").write_text("not a report")

    site = tmp_path / "site"
    kept = build_site(report, previous, site, datetime(2026, 11, 21, 9, 30, tzinfo=timezone.utc), 14)

    assert [p.name for p in kept] == ["2026-11-21T0930Z.html", "2026-11-10T0900Z.html"]
    assert sorted(p.name for p in (site / "reports").iterdir()) == ["2026-11-10T0900Z.html", "2026-11-21T0930Z.html"]
    assert (site / "index.html").read_text() == "new"
    assert "2026-11-10T0900Z" in (site / "archive.html").read_text()
    assert (site / ".nojekyll").exists()


def test_build_site_without_previous_site(tmp_path: Path) -> None:
    report = tmp_path / "report.html"
    report.write_text("first")
    kept = build_site(report, None, tmp_path / "site", datetime(2026, 11, 21, tzinfo=timezone.utc), 14)
    assert len(kept) == 1
