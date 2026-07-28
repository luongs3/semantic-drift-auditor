"""Tests for the terminal renderer.

Added after vision-QA on the demo frames showed the tool dumping raw markdown (`**`,
`##`, ```` ```diff ````) straight to a TTY — which makes a working tool read as
unfinished output. The renderer that fixes that then introduced its own bug, so both
directions are pinned here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from report import to_terminal  # noqa: E402

SAMPLE = (
    "# Semantic Drift Audit\n\n"
    "Scanned **10** glossary term(s).\n\n"
    "---\n\n"
    "## 🔴 Order Total — MEANING CHANGED\n"
    "Definition changed by **`__datahub_system`** at 2026-07-28 (revision v5 → v6).\n"
    "- _(+2 related signal(s))_\n\n"
    "```diff\n"
    "--- v5 (older)\n"
    "-The total value, including tax.\n"
    "+The net value, excluding tax.\n"
    "```\n\n"
    "> Every one of those surfaces still renders without error.\n"
)


def test_markdown_syntax_is_not_shown_to_the_terminal():
    out = to_terminal(SAMPLE, color=False)
    assert "**" not in out
    assert "```" not in out
    assert not any(line.startswith("#") for line in out.splitlines())


def test_identifiers_keep_their_underscores():
    """The regression: `_(.+?)_` ate `__datahub_system` down to `_datahubsystem`."""
    out = to_terminal(SAMPLE, color=False)
    assert "__datahub_system" in out
    assert "_datahubsystem" not in out


def test_snake_case_in_a_diff_is_untouched():
    out = to_terminal("```diff\n-SUM(order_total) FROM order_entry_db\n```\n", color=False)
    assert "order_total" in out
    assert "order_entry_db" in out


def test_genuine_italics_are_still_unwrapped():
    out = to_terminal("- _(+2 related signal(s))_\n", color=False)
    assert "_" not in out
    assert "(+2 related signal(s))" in out


def test_diff_lines_are_coloured():
    out = to_terminal(SAMPLE, color=True)
    assert "\033[32m+The net value" in out   # added -> green
    assert "\033[31m-The total value" in out  # removed -> red


def test_no_ansi_when_colour_is_disabled():
    assert "\033[" not in to_terminal(SAMPLE, color=False)


def test_content_survives_rendering():
    """Stripping syntax must not drop the actual findings."""
    out = to_terminal(SAMPLE, color=False)
    for fragment in ["Semantic Drift Audit", "Order Total", "MEANING CHANGED",
                     "The total value, including tax.", "The net value, excluding tax."]:
        assert fragment in out
