"""Rendering a finding into something a human reads and a graph stores.

Two audiences, one analysis:

* ``render_markdown`` — what a data engineer sees in the terminal or a PR comment.
* ``render_document`` — what gets written back into DataHub as a Context Document, so the
  next person to open the affected dashboard inherits the finding instead of rediscovering it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from drift import DriftFinding, Severity

_ICON = {
    Severity.BREAKING: "🔴",
    Severity.CLARIFYING: "🟡",
    Severity.COSMETIC: "⚪",
}

_HEADLINE = {
    Severity.BREAKING: "MEANING CHANGED",
    Severity.CLARIFYING: "definition clarified",
    Severity.COSMETIC: "wording only",
}


def _when(timestamp: int) -> str:
    if not timestamp:
        return "unknown time"
    return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _actor(actor: str) -> str:
    """`urn:li:corpuser:jdoe` -> `jdoe`."""
    return actor.rsplit(":", 1)[-1] if actor else "unknown"


def _bullets(items: list[str], limit: int = 8) -> str:
    if not items:
        return "  _none_\n"
    shown = items[:limit]
    out = "".join(f"  - {i}\n" for i in shown)
    if len(items) > limit:
        out += f"  - …and {len(items) - limit} more\n"
    return out


def render_markdown(finding: DriftFinding) -> str:
    """The human-facing report."""
    sev = finding.verdict.severity
    lines: list[str] = []
    lines.append(f"## {_ICON[sev]} {finding.term_name} — {_HEADLINE[sev]}\n")
    lines.append(
        f"Definition changed by **{_actor(finding.actor)}** at {_when(finding.changed_at)} "
        f"(revision v{finding.diff.old_version} → v{finding.diff.new_version}).\n"
    )

    if finding.verdict.reasons:
        lines.append("**Why this classification:**\n")
        lines.extend(f"- {r}\n" for r in finding.verdict.reasons)
        lines.append("\n")

    if not finding.diff.is_empty:
        lines.append("**What changed:**\n\n```diff\n")
        lines.append(finding.diff.unified(finding.old_definition, finding.new_definition))
        lines.append("\n```\n\n")

    impact = finding.impact
    lines.append(
        f"**Blast radius** — applied to {impact.direct_assets} asset(s), which feed "
        f"{impact.consumer_count} consumer surface(s):\n\n"
    )
    lines.append(f"- **{len(impact.dashboards)} dashboard(s)**\n")
    lines.append(_bullets(impact.dashboards))
    lines.append(f"- **{len(impact.charts)} chart(s)**\n")
    lines.append(_bullets(impact.charts))
    lines.append(f"- {len(impact.datasets)} downstream dataset(s)\n")

    # Only name people when there is actually something to tell them. Listing "owners to
    # notify" directly above "no action needed" is a contradiction a reader notices.
    if impact.owners and finding.should_alert:
        lines.append(f"\n**Owners to notify:** {', '.join(impact.owners)}\n")

    lines.append("\n")
    if finding.should_alert:
        lines.append(
            f"> Every one of those {impact.consumer_count} surfaces still renders without error. "
            "They now answer a different question than they did before this edit, and nothing "
            "in the stack said so.\n"
        )
    elif sev is Severity.BREAKING:
        lines.append("> Meaning moved, but nothing consumes this term yet. No one to notify.\n")
    else:
        lines.append("> No action needed — the computation is unchanged.\n")

    return "".join(lines)


def render_document(finding: DriftFinding) -> tuple[str, str]:
    """(title, body) for the Context Document written back into DataHub.

    Kept plain and self-contained: someone reading this inside DataHub six months from
    now has no access to our terminal output, so the document restates the before/after
    rather than referring to it.
    """
    sev = finding.verdict.severity
    title = f"Semantic drift: {finding.term_name} ({sev.value})"

    body: list[str] = []
    body.append(
        f"The glossary term '{finding.term_name}' was redefined by {_actor(finding.actor)} "
        f"at {_when(finding.changed_at)}. semantic-drift-auditor classified this change as "
        f"{sev.value}.\n\n"
    )
    if finding.verdict.reasons:
        body.append("Signals: " + "; ".join(finding.verdict.reasons) + ".\n\n")

    body.append("PREVIOUS DEFINITION\n")
    body.append(_truncate(finding.old_definition) + "\n\n")
    body.append("CURRENT DEFINITION\n")
    body.append(_truncate(finding.new_definition) + "\n\n")

    impact = finding.impact
    body.append(
        f"IMPACT: applied to {impact.direct_assets} asset(s), feeding "
        f"{len(impact.dashboards)} dashboard(s) and {len(impact.charts)} chart(s) "
        f"across {len(impact.datasets)} downstream dataset(s).\n"
    )
    if impact.dashboards:
        body.append("Dashboards: " + ", ".join(impact.dashboards[:12]) + "\n")
    if impact.charts:
        body.append("Charts: " + ", ".join(impact.charts[:12]) + "\n")
    if impact.owners:
        body.append("Owners: " + ", ".join(impact.owners) + "\n")

    if finding.should_alert:
        body.append(
            "\nThese surfaces did not fail and were not regenerated. They render the same as "
            "before while answering a different question. Reconcile any figure quoted from them "
            "before this timestamp against the previous definition above.\n"
        )
    return title, "".join(body)


def _truncate(text: str, limit: int = 1500) -> str:
    text = (text or "").replace("\\_", "_").replace("\xa0", " ").strip()
    return text if len(text) <= limit else text[:limit] + " […]"


def render_summary(findings: list[DriftFinding], scanned: int) -> str:
    """One-screen roll-up across every term audited."""
    alerting = [f for f in findings if f.should_alert]
    breaking = [f for f in findings if f.verdict.severity is Severity.BREAKING]

    lines = ["# Semantic Drift Audit\n\n"]
    lines.append(
        f"Scanned **{scanned}** glossary term(s); **{len(findings)}** had revision history; "
        f"**{len(breaking)}** changed meaning; **{len(alerting)}** of those have live consumers.\n\n"
    )
    if not findings:
        lines.append(
            "No term in this catalog has more than one revision, so there is nothing to compare "
            "yet. Run `make drift` to author a definition change through DataHub's API and "
            "re-run the audit.\n"
        )
        return "".join(lines)

    lines.append("| Term | Verdict | Dashboards | Charts | Alert |\n")
    lines.append("|---|---|---|---|---|\n")
    for f in sorted(findings, key=lambda x: (-x.verdict.severity.rank, x.term_name)):
        lines.append(
            f"| {f.term_name} | {_ICON[f.verdict.severity]} {f.verdict.severity.value} "
            f"| {len(f.impact.dashboards)} | {len(f.impact.charts)} "
            f"| {'YES' if f.should_alert else '—'} |\n"
        )
    lines.append("\n---\n\n")
    for f in sorted(findings, key=lambda x: (-x.verdict.severity.rank, x.term_name)):
        lines.append(render_markdown(f))
        lines.append("\n---\n\n")
    return "".join(lines)
