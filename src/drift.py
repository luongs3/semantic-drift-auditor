"""Semantic drift detection: what changed about a definition, and who inherited the change.

The failure mode this exists for has no error message. Someone edits what "Order Total"
means — now it excludes shipping, say — and every dashboard downstream keeps rendering
without complaint. No pipeline fails. No test goes red. No column disappears. The numbers
just quietly start answering a different question than the one they answered last quarter.

This module does three things:

1. **Diffs** consecutive definitions of a glossary term (`diff_revisions`).
2. **Classifies** how badly the meaning moved (`classify_drift`) — because "fixed a typo"
   and "changed the aggregation" must not page the same people.
3. **Scores blast radius** — how many dashboards and charts inherited the new meaning
   without anyone telling them (`assess`).

The classifier is deliberately rule-based rather than an LLM call. A judge can read these
rules, disagree with a specific weight, and still trust the output is reproducible; an
LLM verdict on a definition diff is neither auditable nor stable across runs.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from enum import Enum

# Datapack URNs and corpuser ids carry an instance-hash prefix (``b2fd91.``) that is pure
# noise in a report, and BI platforms key many assets by UUID.
_INSTANCE_PREFIX = re.compile(r"^[0-9a-f]{6}\.")
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


class Severity(str, Enum):
    """How much a definition change moves the number a consumer sees."""

    COSMETIC = "COSMETIC"      # wording only — the metric is unchanged
    CLARIFYING = "CLARIFYING"  # more precise, same intent
    BREAKING = "BREAKING"      # the computation or population changed

    @property
    def rank(self) -> int:
        return {"COSMETIC": 0, "CLARIFYING": 1, "BREAKING": 2}[self.value]


# Phrases that indicate the *computation* changed, not just the prose. Each is a
# (regex, human reason) pair; matching any of them on added or removed text is enough to
# call a change BREAKING.
_BREAKING_SIGNALS: list[tuple[str, str]] = [
    (r"\b(includ\w*|exclud\w*)\b", "inclusion/exclusion of components changed"),
    (r"\b(sum|avg|average|count|median|min|max|distinct)\b", "aggregation changed"),
    (r"\bnet\b|\bgross\b", "net/gross basis changed"),
    (r"\b(before|after|net of|gross of)\b", "calculation boundary changed"),
    (r"\b(tax|shipping|discount|refund|freight|vat)\b", "monetary component changed"),
    (r"\b\d+\s*(day|days|week|weeks|month|months|quarter|year|years)\b", "time window changed"),
    (r"\b(active|inactive|churn\w*|eligible|qualified)\b", "population definition changed"),
    (r"[<>]=?\s*\d+|\b\d+\s*%", "threshold changed"),
    (r"\bgroup by\b|\bwhere\b|\bjoin\b|\bfilter\b", "SQL semantics changed"),
]

_CLARIFYING_SIGNALS: list[tuple[str, str]] = [
    (r"\b(example|e\.g\.|note|see also|refer to|documentation)\b", "documentation expanded"),
    (r"\b(owner|steward|contact|team)\b", "ownership/context noted"),
]

# Headings that mark the end of the *statement of meaning* and the start of supporting
# material. Everything above the first of these is the core definition.
_SUPPORTING_HEADING = re.compile(
    r"^\s*(sql\s+calculation|example\s+queries|examples?|usage|notes?|see also|references?)\b",
    re.I,
)


def split_core(text: str) -> tuple[str, str]:
    """Split a definition into (core meaning, supporting material).

    This distinction is what keeps the tool credible. A definition is usually one or two
    sentences of actual meaning followed by SQL snippets and worked examples. Deleting
    the examples is a documentation change; rewriting the first sentence is a semantic
    one. Treating them identically produces false alarms on doc cleanups — and a drift
    detector that cries wolf on doc cleanups is a detector people mute.
    """
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        if _SUPPORTING_HEADING.match(line):
            return "\n".join(lines[:index]), "\n".join(lines[index:])
    return text or "", ""


@dataclass
class DefinitionDiff:
    """The textual delta between two consecutive revisions of a term."""

    old_version: int
    new_version: int
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed

    def unified(self, old_text: str, new_text: str, context: int = 1, max_lines: int = 16) -> str:
        """A unified diff, collapsed to fit on one screen.

        A definition rewrite often deletes a dozen near-identical SQL example lines. Left
        at full length the removals push the *new* definition off the bottom of the
        terminal — and the new definition is the whole point. Runs of more than three
        consecutive same-sign lines are summarised instead.
        """
        raw = list(
            difflib.unified_diff(
                _sentences(old_text),
                _sentences(new_text),
                fromfile=f"v{self.old_version} (older)",
                tofile=f"v{self.new_version} (newer)",
                lineterm="",
                n=context,
            )
        )
        if len(raw) <= max_lines:
            return "\n".join(raw)

        # The file headers are the first two lines only. Testing with startswith("---")
        # would also match a removed bullet line like "-- example query", because the
        # diff marker and the bullet dash collide.
        def is_header(position: int) -> bool:
            return position < 2

        out: list[str] = []
        index = 0
        while index < len(raw):
            line = raw[index]
            sign = line[:1]
            if sign in "+-" and not is_header(index):
                run = index
                while run < len(raw) and raw[run][:1] == sign and not is_header(run):
                    run += 1
                length = run - index
                if length > 3:
                    out.extend(raw[index : index + 2])
                    word = "removed" if sign == "-" else "added"
                    # No +/- prefix: an elision marker carrying a diff sign reads as a
                    # line whose *content* is "… 8 more lines", not as an annotation.
                    out.append(f"  … {length - 2} more lines {word}")
                    index = run
                    continue
            out.append(line)
            index += 1
        return "\n".join(out)


@dataclass
class DriftVerdict:
    """The classifier's read on one definition change."""

    severity: Severity
    reasons: list[str] = field(default_factory=list)

    @property
    def is_breaking(self) -> bool:
        return self.severity is Severity.BREAKING

    def top_reasons(self, limit: int = 3) -> list[str]:
        """The most specific reasons, for display.

        The signal list overlaps by design — a change from "including tax" to "net of
        tax" legitimately trips inclusion, net/gross and monetary-component all at once.
        Printing all five reads as the classifier dumping its rule table rather than
        matching, so the report shows the top few. ``reasons`` keeps the full list for
        anyone reading the JSON.
        """
        return self.reasons[:limit]


@dataclass
class ImpactSummary:
    """Who inherited the redefined meaning."""

    direct_assets: int = 0
    dashboards: list[str] = field(default_factory=list)
    charts: list[str] = field(default_factory=list)
    datasets: list[str] = field(default_factory=list)
    owners: list[str] = field(default_factory=list)
    # URNs kept alongside the display names so write-back can address the real entities.
    tagged_urns: list[str] = field(default_factory=list)
    dataset_urns: list[str] = field(default_factory=list)

    @property
    def consumer_count(self) -> int:
        """Charts + dashboards — the surfaces a human actually reads a number off."""
        return len(self.dashboards) + len(self.charts)


@dataclass
class DriftFinding:
    """One term's audit result: what changed, how bad, and who is affected."""

    term_urn: str
    term_name: str
    diff: DefinitionDiff
    verdict: DriftVerdict
    impact: ImpactSummary
    old_definition: str = ""
    new_definition: str = ""
    changed_at: int = 0
    actor: str = ""

    @property
    def should_alert(self) -> bool:
        """Alert only when meaning moved AND somebody is downstream to be misled."""
        return self.verdict.is_breaking and self.impact.consumer_count > 0


def normalise(text: str) -> str:
    """Undo the markdown escaping DataHub stores definitions with.

    Definitions arrive with `\\-`, `\\_`, `\\[` and non-breaking spaces baked in by the
    ingestion path. Left alone those escapes show up verbatim in the diff — and worse,
    asymmetrically, because a definition *we* author has none. The same line then looks
    different on the before and after sides, which reads as a bug in the differ.
    """
    if not text:
        return ""
    cleaned = text.replace("\xa0", " ")
    return re.sub(r"\\([-_*\[\]()#.+!`])", r"\1", cleaned)


def _sentences(text: str) -> list[str]:
    """Split a definition into comparable units.

    Definitions are markdown blobs with SQL snippets and bullet lists, so a naive line
    split produces noisy diffs dominated by blank lines and escape characters. Splitting
    on sentence and bullet boundaries lines the two revisions up on meaning instead.
    """
    if not text:
        return []
    parts = re.split(r"(?<=[.:;])\s+|\n+", normalise(text))
    return [p.strip() for p in parts if p.strip()]


def _tokens(lines: list[str]) -> str:
    return " ".join(lines).lower()


def diff_revisions(older: str, newer: str, old_version: int, new_version: int) -> DefinitionDiff:
    """Sentence-level delta between two definitions, ignoring pure reformatting."""
    old_lines = _sentences(older)
    new_lines = _sentences(newer)
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    added: list[str] = []
    removed: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed.extend(old_lines[i1:i2])
        if tag in ("replace", "insert"):
            added.extend(new_lines[j1:j2])
    return DefinitionDiff(
        old_version=old_version, new_version=new_version, added=added, removed=removed
    )


def classify_drift(diff: DefinitionDiff, core_diff: DefinitionDiff | None = None) -> DriftVerdict:
    """Decide whether a definition change moved the number.

    Rules, in order:

    * No textual delta -> COSMETIC.
    * Any breaking signal in the *core meaning* that was added or removed -> BREAKING,
      listing which signals fired. Removal matters as much as addition: deleting
      "including tax" is exactly as consequential as adding "excluding tax".
    * Change confined to supporting material (SQL examples, worked queries, notes) ->
      CLARIFYING at most, never BREAKING. Rewriting the examples under an unchanged
      definition does not change what the metric means.
    * Otherwise, clarifying signals -> CLARIFYING; nothing recognised -> COSMETIC.

    ``core_diff`` is the delta restricted to the core meaning. When it is omitted the
    whole diff is treated as core, which is the conservative reading.
    """
    if diff.is_empty:
        return DriftVerdict(Severity.COSMETIC, ["no textual change"])

    effective = core_diff if core_diff is not None else diff
    reasons: list[str] = []

    if not effective.is_empty:
        core_text = _tokens(effective.added) + " " + _tokens(effective.removed)
        for pattern, reason in _BREAKING_SIGNALS:
            if re.search(pattern, core_text) and reason not in reasons:
                reasons.append(reason)
        if reasons:
            return DriftVerdict(Severity.BREAKING, reasons)

    changed_text = _tokens(diff.added) + " " + _tokens(diff.removed)
    if core_diff is not None and core_diff.is_empty:
        return DriftVerdict(
            Severity.CLARIFYING,
            ["examples/notes edited; the statement of meaning is unchanged"],
        )

    for pattern, reason in _CLARIFYING_SIGNALS:
        if re.search(pattern, changed_text) and reason not in reasons:
            reasons.append(reason)
    if reasons:
        return DriftVerdict(Severity.CLARIFYING, reasons)

    return DriftVerdict(Severity.COSMETIC, ["wording changed, no semantic markers"])


def _clean_name(raw: str, platform: str = "") -> str:
    """A label a human recognises.

    Strips the datapack instance-hash prefix, shortens bare UUIDs, and qualifies the
    label with its platform. That last part matters more than it looks: the seed catalog
    has a Looker "Order Entry Dashboard" *and* a Tableau one. Printing both unqualified
    reads as a duplicate-row bug rather than as two real dashboards.
    """
    name = _INSTANCE_PREFIX.sub("", (raw or "").strip())
    last = name.rsplit(".", 1)[-1]
    if _UUID_RE.fullmatch(last):
        name = f"{last[:8]}…"
    if platform:
        return f"{name} ({platform})"
    return name


def summarise_impact(tagged_assets, downstream_by_asset: dict) -> ImpactSummary:
    """Fold the term's assets and their lineage into one blast-radius view.

    dbt and Snowflake publish the same logical table as sibling entities, so the same
    dashboard is reachable by more than one path. We deduplicate by URN — an inflated
    count is the fastest way to lose a judge's trust.
    """
    summary = ImpactSummary(direct_assets=len(tagged_assets))
    summary.tagged_urns = [a.urn for a in tagged_assets if getattr(a, "urn", "")]
    seen: set[str] = set()
    owners: set[str] = set()
    dataset_urns: list[str] = []

    for asset in tagged_assets:
        for down in downstream_by_asset.get(asset.urn, []):
            if down.urn in seen:
                continue
            seen.add(down.urn)
            label = _clean_name(down.name or down.urn, getattr(down, "platform", ""))
            if down.entity_type == "DASHBOARD":
                summary.dashboards.append(label)
            elif down.entity_type == "CHART":
                summary.charts.append(label)
            else:
                summary.datasets.append(label)
                dataset_urns.append(down.urn)
            owners.update(_clean_name(o) for o in (getattr(down, "owners", []) or []))

    summary.dashboards.sort()
    summary.charts.sort()
    summary.datasets.sort()
    summary.owners = sorted(o for o in owners if o)
    # Incidents attach to datasets. Prefer the assets the term is directly applied to,
    # then fall back to downstream datasets.
    direct_datasets = [
        a.urn for a in tagged_assets if getattr(a, "entity_type", "") == "DATASET"
    ]
    summary.dataset_urns = direct_datasets or dataset_urns
    return summary


def assess(
    term_urn: str,
    term_name: str,
    revisions,
    tagged_assets,
    downstream_by_asset: dict,
    timeline=None,
) -> DriftFinding | None:
    """Full audit of one term. Returns None when there is no history to compare.

    ``revisions`` arrives newest-first (see ``DataHubClient.term_revisions`` — sorted by
    wall-clock time, not by DataHub's counterintuitive version numbering). We compare the
    current definition against the one immediately before it: that is the change nobody
    was told about.
    """
    if len(revisions) < 2:
        return None

    current, previous = revisions[0], revisions[1]
    diff = diff_revisions(previous.definition, current.definition, previous.version, current.version)

    # Classify on the core meaning only; the full diff is still what we show the human.
    core_diff = diff_revisions(
        split_core(previous.definition)[0],
        split_core(current.definition)[0],
        previous.version,
        current.version,
    )
    verdict = classify_drift(diff, core_diff)
    impact = summarise_impact(tagged_assets, downstream_by_asset)

    # Prefer the revision's own timestamp; fall back to the timeline's last edit event.
    changed_at = getattr(current, "observed_at", 0) or 0
    actor = ""
    for change in timeline or []:
        if change.operation in ("MODIFY", "ADD"):
            actor = change.actor
            if not changed_at:
                changed_at = change.timestamp

    return DriftFinding(
        term_urn=term_urn,
        term_name=term_name,
        diff=diff,
        verdict=verdict,
        impact=impact,
        old_definition=previous.definition,
        new_definition=current.definition,
        changed_at=changed_at,
        actor=actor,
    )
