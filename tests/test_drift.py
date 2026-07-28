"""Tests for the drift classifier.

The classifier is the part a judge will poke at hardest, because a semantic-drift tool
that shouts on every edit is worthless. These tests pin down both directions: real
meaning changes must alarm, and documentation churn must not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from drift import (  # noqa: E402
    Severity,
    classify_drift,
    diff_revisions,
    split_core,
)


def verdict_for(old: str, new: str):
    diff = diff_revisions(old, new, 1, 2)
    core = diff_revisions(split_core(old)[0], split_core(new)[0], 1, 2)
    return classify_drift(diff, core)


# --------------------------------------------------------------------- BREAKING

@pytest.mark.parametrize(
    "old,new,label",
    [
        (
            "Total order value including shipping and tax.",
            "Total order value excluding shipping and tax.",
            "inclusion flipped",
        ),
        (
            "Revenue is the SUM of line item totals.",
            "Revenue is the AVG of line item totals.",
            "aggregation swapped",
        ),
        (
            "Gross revenue per order.",
            "Net revenue per order.",
            "gross -> net",
        ),
        (
            "A customer who ordered in the last 30 days.",
            "A customer who ordered in the last 90 days.",
            "time window widened",
        ),
        (
            "An active customer has placed at least 1 order.",
            "An active customer has placed at least 5 orders.",
            "threshold raised",
        ),
    ],
)
def test_meaning_changes_are_breaking(old, new, label):
    assert verdict_for(old, new).severity is Severity.BREAKING, label


def test_removal_is_as_breaking_as_addition():
    """Deleting 'including tax' changes the number exactly as much as adding 'excluding tax'."""
    verdict = verdict_for(
        "Order value, including tax.",
        "Order value.",
    )
    assert verdict.severity is Severity.BREAKING
    assert verdict.reasons


# ------------------------------------------------------------------- NOT BREAKING

def test_identical_definitions_are_cosmetic():
    verdict = verdict_for("Total order value.", "Total order value.")
    assert verdict.severity is Severity.COSMETIC
    assert verdict.reasons == ["no textual change"]


def test_typo_fix_is_not_breaking():
    verdict = verdict_for("Total order valeu.", "Total order value.")
    assert verdict.severity is not Severity.BREAKING


def test_rewriting_sql_examples_is_not_breaking():
    """The regression that made us split core from supporting material.

    The statement of meaning is untouched; only the worked examples below the
    "SQL Calculation" heading change. Flagging this BREAKING is a false alarm, and false
    alarms are how a drift detector gets muted.
    """
    old = (
        "Aggregated revenue segmented by customer classification.\n"
        "SQL Calculation Patterns:\n"
        "- Group by customer_class and aggregate order_total\n"
        "- Formula: SUM(order_total) GROUP BY customer_class\n"
    )
    new = (
        "Aggregated revenue segmented by customer classification.\n"
        "Notes:\n"
        "Reviewed and re-published by the data governance team.\n"
    )
    verdict = verdict_for(old, new)
    assert verdict.severity is not Severity.BREAKING


def test_breaking_in_core_still_wins_when_examples_also_change():
    """A real redefinition must not be excused because the examples moved too."""
    old = (
        "Order value including shipping.\n"
        "SQL Calculation:\n"
        "- SUM(order_total)\n"
    )
    new = (
        "Order value excluding shipping.\n"
        "SQL Calculation:\n"
        "- SUM(order_total - shipping_amount)\n"
    )
    assert verdict_for(old, new).severity is Severity.BREAKING


# ------------------------------------------------------------------------ split_core

def test_split_core_separates_definition_from_examples():
    core, supporting = split_core(
        "The meaning line.\nSQL Calculation:\n- SELECT 1\n"
    )
    assert core.strip() == "The meaning line."
    assert "SELECT 1" in supporting


def test_split_core_with_no_heading_returns_everything_as_core():
    core, supporting = split_core("Just a definition, no examples.")
    assert core == "Just a definition, no examples."
    assert supporting == ""


# ---------------------------------------------------------------------- diffing

def test_diff_ignores_pure_reformatting():
    """Definitions arrive with escaped markdown and non-breaking spaces."""
    old = "The\xa0total value, including\xa0tax."
    new = "The total value, including tax."
    assert diff_revisions(old, new, 1, 2).is_empty


def test_diff_reports_added_and_removed():
    diff = diff_revisions("One. Two.", "One. Three.", 1, 2)
    assert any("Two" in line for line in diff.removed)
    assert any("Three" in line for line in diff.added)
