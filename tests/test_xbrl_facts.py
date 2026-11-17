from datetime import date
from typing import Any

from xbrl_facts import (
    first_quarterly_series,
    instance_dimensional_value,
    instant_series,
    quarterly_series,
    trailing_sum,
)


def fact(val: float, end: str, filed: str, accn: str, start: str | None = None, form: str = "10-Q") -> dict[str, Any]:
    out: dict[str, Any] = {"val": val, "end": end, "filed": filed, "accn": accn, "form": form}
    if start:
        out["start"] = start
    return out


def payload(tag: str, facts: list[dict[str, Any]], unit: str = "USD") -> dict[str, Any]:
    return {"facts": {"us-gaap": {tag: {"units": {unit: facts}}}}}


def test_quarterly_series_derives_quarters_from_year_to_date() -> None:
    p = payload("Pik", [
        fact(52, "2026-03-31", "2026-04-28", "q1", start="2026-01-01"),
        fact(110, "2026-06-30", "2026-07-29", "q2", start="2026-01-01"),
        fact(168, "2026-09-30", "2026-10-27", "q3", start="2026-01-01"),
        fact(219, "2025-12-31", "2026-02-10", "k", start="2025-01-01", form="10-K"),
        fact(165, "2025-09-30", "2025-10-28", "q3-25", start="2025-01-01"),
    ])
    q = quarterly_series(p, "Pik")
    assert {d: v[0] for d, v in q.items()} == {
        date(2026, 3, 31): 52, date(2026, 6, 30): 58, date(2026, 9, 30): 58, date(2025, 12, 31): 54,
    }
    assert q[date(2026, 9, 30)][2] == "q3"


def test_instant_series_keeps_original_filing_unless_restated() -> None:
    p = payload("Nav", [
        fact(19.94, "2025-12-31", "2026-02-10", "10k"),
        fact(19.94, "2025-12-31", "2026-10-27", "later-10q"),
        fact(19.59, "2026-03-31", "2026-04-28", "q1"),
        fact(19.60, "2026-03-31", "2026-07-29", "restated"),
    ], unit="USD/shares")
    s = instant_series(p, "Nav", "USD/shares")
    assert s[date(2025, 12, 31)][2] == "10k"
    assert s[date(2026, 3, 31)][::2] == (19.60, "restated")


def test_first_quarterly_series_prefers_earlier_tags() -> None:
    p = {"facts": {"us-gaap": {
        "A": {"units": {"USD": [fact(10, "2026-09-30", "2026-11-01", "a", start="2026-07-01")]}},
        "B": {"units": {"USD": [fact(99, "2026-09-30", "2026-11-01", "b", start="2026-07-01"),
                                fact(7, "2026-06-30", "2026-08-01", "b2", start="2026-04-01")]}},
    }}}
    q = first_quarterly_series(p, ("A", "B"))
    assert q[date(2026, 9, 30)][1] == "A"
    assert q[date(2026, 6, 30)][1] == "B"


def test_trailing_sum_requires_consecutive_quarters() -> None:
    ends = [date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30)]
    q = {d: ((1.0, d, "x"), "T") for d in ends}
    assert trailing_sum(q, date(2026, 9, 30)) == 4.0
    del q[date(2026, 3, 31)]
    assert trailing_sum(q, date(2026, 9, 30)) is None


INSTANCE = b"""<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
  xmlns:us-gaap="http://fasb.org/us-gaap/2025">
  <xbrli:context id="l3"><xbrli:entity><xbrli:segment>
    <xbrldi:explicitMember dimension="us-gaap:FairValueByFairValueHierarchyLevelAxis">us-gaap:FairValueInputsLevel3Member</xbrldi:explicitMember>
    <xbrldi:explicitMember dimension="us-gaap:FairValueByMeasurementFrequencyAxis">us-gaap:FairValueMeasurementsRecurringMember</xbrldi:explicitMember>
  </xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="l3-vie"><xbrli:entity><xbrli:segment>
    <xbrldi:explicitMember dimension="us-gaap:FairValueByFairValueHierarchyLevelAxis">us-gaap:FairValueInputsLevel3Member</xbrldi:explicitMember>
    <xbrldi:explicitMember dimension="us-gaap:ConsolidatedEntitiesAxis">us-gaap:VariableInterestEntityPrimaryBeneficiaryMember</xbrldi:explicitMember>
  </xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="l3-prior"><xbrli:entity><xbrli:segment>
    <xbrldi:explicitMember dimension="us-gaap:FairValueByFairValueHierarchyLevelAxis">us-gaap:FairValueInputsLevel3Member</xbrldi:explicitMember>
  </xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
  <us-gaap:AssetsFairValueDisclosure contextRef="l3">177649000000</us-gaap:AssetsFairValueDisclosure>
  <us-gaap:AssetsFairValueDisclosure contextRef="l3-vie">5000000000</us-gaap:AssetsFairValueDisclosure>
  <us-gaap:AssetsFairValueDisclosure contextRef="l3-prior">150000000000</us-gaap:AssetsFairValueDisclosure>
</xbrli:xbrl>"""


def test_instance_dimensional_value_matches_only_allowed_dimensions() -> None:
    value = instance_dimensional_value(
        INSTANCE, "AssetsFairValueDisclosure", "2026-09-30",
        {"FairValueByFairValueHierarchyLevelAxis": "FairValueInputsLevel3Member"},
        {"FairValueByMeasurementFrequencyAxis": "FairValueMeasurementsRecurringMember"},
    )
    assert value == 177649000000
    assert instance_dimensional_value(INSTANCE, "AssetsFairValueDisclosure", "2026-06-30",
                                      {"FairValueByFairValueHierarchyLevelAxis": "FairValueInputsLevel3Member"}) is None
