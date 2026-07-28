"""Tests for blast-radius aggregation and report rendering.

These use stub assets rather than a live DataHub, which is the point: the analysis has to
be verifiable without standing up seven containers.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from drift import (  # noqa: E402
    DriftFinding,
    DriftVerdict,
    Severity,
    _clean_name,
    diff_revisions,
    summarise_impact,
)
from report import render_document, render_markdown, render_summary  # noqa: E402


@dataclass
class StubAsset:
    urn: str
    entity_type: str = "DATASET"
    degree: int = 1
    name: str = ""
    platform: str = ""
    owners: list[str] = field(default_factory=list)


def make_finding(severity=Severity.BREAKING, dashboards=2, charts=3) -> DriftFinding:
    tagged = [StubAsset("urn:li:dataset:(x,orders,PROD)", name="orders")]
    downstream = {
        tagged[0].urn: (
            [
                StubAsset(f"urn:li:dashboard:(looker,d{i})", "DASHBOARD", 2, f"Dash {i}", "looker")
                for i in range(dashboards)
            ]
            + [
                StubAsset(f"urn:li:chart:(tableau,c{i})", "CHART", 3, f"Chart {i}", "tableau")
                for i in range(charts)
            ]
        )
    }
    return DriftFinding(
        term_urn="urn:li:glossaryTerm:b2fd91.order-total",
        term_name="Order Total",
        diff=diff_revisions("Value including tax.", "Value excluding tax.", 1, 2),
        verdict=DriftVerdict(severity, ["inclusion/exclusion of components changed"]),
        impact=summarise_impact(tagged, downstream),
        old_definition="Value including tax.",
        new_definition="Value excluding tax.",
        changed_at=1785209795873,
        actor="urn:li:corpuser:jdoe",
    )


# ---------------------------------------------------------------- blast radius

def test_sibling_paths_are_deduplicated():
    """dbt and Snowflake publish the same logical table, so the same dashboard is
    reachable twice. Counting it twice is the fastest way to lose a judge's trust."""
    dash = StubAsset("urn:li:dashboard:(looker,d1)", "DASHBOARD", 2, "Exec", "looker")
    tagged = [StubAsset("urn:li:dataset:(dbt,orders,PROD)"), StubAsset("urn:li:dataset:(snowflake,orders,PROD)")]
    downstream = {tagged[0].urn: [dash], tagged[1].urn: [dash]}
    impact = summarise_impact(tagged, downstream)
    assert impact.dashboards == ["Exec (looker)"]
    assert impact.consumer_count == 1


def test_assets_are_split_by_kind():
    impact = make_finding(dashboards=2, charts=3).impact
    assert len(impact.dashboards) == 2
    assert len(impact.charts) == 3
    assert impact.consumer_count == 5


def test_owners_are_collected_and_deduplicated():
    tagged = [StubAsset("urn:li:dataset:(x,orders,PROD)")]
    downstream = {
        tagged[0].urn: [
            StubAsset("urn:li:dashboard:(looker,d1)", "DASHBOARD", 2, "A", "looker", ["b2fd91.sam@e.com"]),
            StubAsset("urn:li:dashboard:(looker,d2)", "DASHBOARD", 2, "B", "looker", ["b2fd91.sam@e.com"]),
        ]
    }
    assert summarise_impact(tagged, downstream).owners == ["sam@e.com"]


# ------------------------------------------------------------------- alerting

def test_breaking_with_consumers_alerts():
    assert make_finding(Severity.BREAKING, dashboards=1, charts=0).should_alert


def test_breaking_without_consumers_does_not_alert():
    """Meaning moved, but nobody is downstream. Nothing to warn about."""
    assert not make_finding(Severity.BREAKING, dashboards=0, charts=0).should_alert


def test_clarifying_never_alerts_even_with_consumers():
    assert not make_finding(Severity.CLARIFYING, dashboards=5, charts=5).should_alert


# ---------------------------------------------------------------------- naming

def test_instance_hash_prefix_is_stripped():
    assert _clean_name("b2fd91.order_details") == "order_details"


def test_bare_uuid_is_shortened():
    assert _clean_name("b2fd91.843bf583-900b-f1ba-0532-b5e67a0373dc").startswith("843bf583")


def test_platform_disambiguates_identically_named_assets():
    """The seed catalog has a Looker AND a Tableau 'Order Entry Dashboard'. Unqualified,
    two real rows read as a duplicate-row bug."""
    assert _clean_name("Order Entry Dashboard", "looker") != _clean_name("Order Entry Dashboard", "tableau")


# --------------------------------------------------------------------- rendering

def test_markdown_report_states_the_consequence():
    out = render_markdown(make_finding())
    assert "MEANING CHANGED" in out
    assert "jdoe" in out            # actor resolved from the urn
    assert "2026-07-28" in out      # timestamp rendered, not a raw epoch
    assert "still renders without error" in out


def test_markdown_report_shows_the_diff():
    out = render_markdown(make_finding())
    assert "```diff" in out
    assert "-Value including tax." in out
    assert "+Value excluding tax." in out


def test_document_restates_both_definitions():
    """Someone reading this inside DataHub has no access to our terminal output."""
    title, body = render_document(make_finding())
    assert "Order Total" in title
    assert "PREVIOUS DEFINITION" in body and "Value including tax." in body
    assert "CURRENT DEFINITION" in body and "Value excluding tax." in body


def test_summary_handles_a_catalog_with_no_history():
    out = render_summary([], scanned=10)
    assert "nothing to compare" in out


def test_summary_ranks_breaking_above_clarifying():
    findings = [make_finding(Severity.CLARIFYING), make_finding(Severity.BREAKING)]
    out = render_summary(findings, scanned=2)
    assert out.index("BREAKING") < out.index("CLARIFYING")
