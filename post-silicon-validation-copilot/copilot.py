"""Post-Silicon Validation Copilot.

Reads a validation plan and the bench logs of what was actually executed, then:

  1. Computes coverage deterministically in Python (no LLM guessing on the numbers).
  2. Asks Claude to turn that structured picture into an executive readout a
     program manager could drop into a gate review -narrative, risk call,
     and a prioritized action list.

This split is deliberate: the math is auditable and reproducible; the LLM only
does what it's good at -judgment, prioritization, and clear communication.

Usage:
    python copilot.py
    python copilot.py --plan sample_data/validation_plan.csv --logs sample_data/bench_logs.csv
"""

import argparse
import csv
import sys
from collections import Counter
from typing import Literal

# Windows consoles default to cp1252 and crash on Unicode; force UTF-8 output.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from pydantic import BaseModel, Field

import jira_adapter
from config import MODEL, get_client


# --------------------------------------------------------------------------- #
# 1. Deterministic coverage analysis (pure Python -the trustworthy part)
# --------------------------------------------------------------------------- #

def load_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def analyze_coverage(plan: list[dict], logs: list[dict]) -> dict:
    """Compute coverage stats from the plan and bench logs."""
    log_by_id = {row["test_id"]: row for row in logs}

    executed, failed, skipped, untested = [], [], [], []
    for test in plan:
        tid = test["test_id"]
        log = log_by_id.get(tid)
        if log is None:
            untested.append(test)
        elif log["status"] == "fail":
            failed.append({**test, **log})
        elif log["status"] == "skip":
            skipped.append({**test, **log})
        else:
            executed.append({**test, **log})

    total = len(plan)
    run = len(executed) + len(failed)  # skip/untested don't count as run
    return {
        "total_planned": total,
        "executed_pass": len(executed),
        "passed": executed,
        "failed": failed,
        "skipped": skipped,
        "untested": untested,
        "coverage_pct": round(100 * run / total, 1) if total else 0.0,
        "pass_rate_pct": round(100 * len(executed) / run, 1) if run else 0.0,
        # Anything critical that is not a clean pass: failures, skips, or untested.
        "critical_gaps": [
            t for t in failed + skipped + untested if t.get("priority") == "critical"
        ],
        "category_counts": dict(Counter(t["category"] for t in plan)),
    }


def assess_confidence(cov: dict) -> tuple[str, str]:
    """Deterministic confidence in the release picture, with reasons.

    Trustworthy-AI principle: the tool should express *calibrated* confidence. You
    cannot be highly confident in a 'go' when a third of the plan is untested or
    untracked — so confidence is computed from data completeness, not guessed.
    """
    reasons = []
    pct = cov["coverage_pct"]
    untracked = cov.get("untracked_in_jira", [])
    ambiguous = cov.get("ambiguous", [])
    stale = cov.get("stale_tickets", [])

    if pct < 80:
        reasons.append(f"only {pct}% of planned tests have a confirmed result")
    if untracked:
        reasons.append(f"{len(untracked)} required test(s) not tracked in Jira")
    if ambiguous:
        reasons.append(f"{len(ambiguous)} closed ticket(s) with no recorded result")
    if stale:
        reasons.append(f"{len(stale)} stale in-flight ticket(s)")

    if pct < 80 or untracked or ambiguous:
        level = "low"
    elif pct < 95 or stale:
        level = "medium"
    else:
        level = "high"
    return level, "; ".join(reasons) or "all planned tests have confirmed, tracked results"


def coverage_by_category(plan: list[dict], passed_ids: set) -> dict:
    """Pass rate per functional area, so weak categories are obvious."""
    planned = Counter(t["category"] for t in plan)
    passed = Counter(t["category"] for t in plan if t["test_id"] in passed_ids)
    return {
        cat: {"planned": n, "passed": passed.get(cat, 0),
              "pct": round(100 * passed.get(cat, 0) / n, 1) if n else 0.0}
        for cat, n in sorted(planned.items())
    }


def render_facts(cov: dict) -> str:
    """Render the computed facts as a compact, LLM-friendly brief."""
    def lines(items, *fields):
        return "\n".join(
            "  - " + " | ".join(f"{f}={i.get(f, '')}" for f in fields) for i in items
        ) or "  (none)"

    base = f"""COVERAGE
  planned={cov['total_planned']}  coverage={cov['coverage_pct']}%  pass_rate={cov['pass_rate_pct']}%

DATA CONFIDENCE: {cov.get('confidence', 'n/a')} ({cov.get('confidence_rationale', '')})

FAILURES
{lines(cov['failed'], 'test_id', 'category', 'priority', 'notes')}

SKIPPED
{lines(cov['skipped'], 'test_id', 'category', 'priority', 'notes')}

UNTESTED (planned, in-flight, no confirmed result)
{lines(cov['untested'], 'test_id', 'category', 'priority', 'description')}

CRITICAL ITEMS NOT YET PASSING
{lines(cov['critical_gaps'], 'test_id', 'category', 'description')}"""

    if cov.get("by_category"):
        cat_lines = "\n".join(
            f"  {cat}: {v['passed']}/{v['planned']} passed ({v['pct']}%)"
            for cat, v in cov["by_category"].items()
        )
        base += f"\n\nCATEGORY COVERAGE\n{cat_lines}"

    # Only present when the source was a Jira export (see jira_adapter.reconcile).
    if "untracked_in_jira" in cov:
        base += f"""

DATA QUALITY - where Jira can't be trusted
  Required tests NOT on the Jira board (untracked):
{lines(cov['untracked_in_jira'], 'test_id', 'category', 'priority', 'description')}
  Closed tickets with NO recorded result (ambiguous - cannot confirm pass):
{lines(cov['ambiguous'], 'test_id', 'category', 'priority', 'notes')}
  Stale tickets (in-flight, not updated recently):
{lines(cov.get('stale_tickets', []), 'test_id', 'category', 'priority', 'notes')}
  Orphan tickets (in Jira, not in the validation plan):
{lines(cov['orphan_tickets'], 'jira_key', 'test_id', 'summary')}"""

    return base


# --------------------------------------------------------------------------- #
# 2. LLM layer -structured executive readout (the judgment part)
# --------------------------------------------------------------------------- #

class Action(BaseModel):
    priority: Literal["P0", "P1", "P2"] = Field(description="P0=blocks release")
    owner_area: str = Field(description="Functional area that should own this, e.g. Analog Design, Test Eng")
    action: str = Field(description="Concrete next step")


class Readout(BaseModel):
    headline: str = Field(description="One-sentence status a director could read in 5 seconds")
    gate_recommendation: Literal["go", "conditional-go", "no-go"]
    confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence in this readout. Adopt the DATA CONFIDENCE level provided "
        "in the facts; do not inflate it.")
    confidence_rationale: str = Field(description="Why the confidence is what it is")
    summary: str = Field(description="2-4 sentence narrative of where validation stands")
    top_risks: list[str] = Field(description="The 2-3 risks that matter most right now")
    tracking_hygiene: list[str] = Field(
        default_factory=list,
        description="Risks specific to Jira/tracking hygiene from the DATA QUALITY section: "
        "required tests not on the board, closed tickets with no result, orphan tickets. "
        "Empty when the facts came from clean bench logs.",
    )
    actions: list[Action] = Field(description="Prioritized, ownable next steps")


SYSTEM = (
    "You are a senior post-silicon validation program manager for mixed-signal/analog ICs. "
    "You are given precomputed, trustworthy coverage facts. Do not recompute or dispute the "
    "numbers. Turn them into a crisp gate-review readout: call the release risk honestly, and "
    "make every action concrete and ownable. Be direct; this goes in front of a director.\n\n"
    "If the facts include a DATA QUALITY section, treat it as real risk, not paperwork: a "
    "critical test that isn't even on the Jira board, or a ticket closed with no recorded "
    "result, is arguably worse than a known failure - it's unverified coverage masquerading "
    "as done. Surface these under tracking_hygiene and let them weigh on the gate call.\n\n"
    "A DATA CONFIDENCE level is provided in the facts. Adopt it verbatim as your confidence "
    "field and echo its reasoning - do not claim high confidence on incomplete or untracked "
    "data. Ground every claim in the facts; never invent a test_id that isn't listed."
)


def generate_readout(facts: str) -> Readout:
    client = get_client()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM,
        messages=[{"role": "user", "content": f"Validation facts:\n\n{facts}"}],
        output_format=Readout,
    )
    return response.parsed_output


# --------------------------------------------------------------------------- #
# 2b. Offline readout -same shape, no API key required (rule-based)
# --------------------------------------------------------------------------- #

def _owner(category: str) -> str:
    analog = {"Audio", "Power", "Trim", "Clocking", "ESD"}
    return "Analog Design" if category in analog else "Test Eng"


def rule_based_readout(cov: dict) -> Readout:
    """A deterministic readout so the tool runs with no API key.

    Same Readout shape as the LLM path -just transparent rules instead of judgment.
    Useful for testing, air-gapped runs, and as a sanity check on the LLM output.
    """
    crit = cov["critical_gaps"]
    untracked = cov.get("untracked_in_jira", [])
    ambiguous = cov.get("ambiguous", [])
    stale = cov.get("stale_tickets", [])
    orphans = cov.get("orphan_tickets", [])

    if crit:
        gate = "no-go"
    elif cov["coverage_pct"] < 90 or untracked or ambiguous:
        gate = "conditional-go"
    else:
        gate = "go"

    risks = [f"Critical {t['category']} item {t['test_id']} is not passing" for t in crit]
    if cov["coverage_pct"] < 90:
        risks.append(f"Coverage at {cov['coverage_pct']}% - not every planned test has a confirmed result")

    hygiene = []
    hygiene += [f"{t['test_id']} ({t['category']}, {t.get('priority')}) is required but not on the Jira board"
                for t in untracked]
    hygiene += [f"{t['test_id']} is closed in Jira with no recorded result - cannot confirm pass"
                for t in ambiguous]
    hygiene += [f"{t['test_id']} ticket is stale (last updated {t.get('updated', '?')})" for t in stale]
    hygiene += [f"{t['jira_key']} ({t['test_id']}) is in Jira but not in the plan" for t in orphans]

    actions = [Action(priority="P0", owner_area=_owner(t["category"]),
                      action=f"Root-cause and re-test {t['test_id']} ({t.get('description', t['category'])})")
               for t in crit]
    actions += [Action(priority="P1", owner_area="Test Eng",
                       action=f"Add {t['test_id']} to the Jira board and execute it") for t in untracked]
    actions += [Action(priority="P1", owner_area="Test Eng",
                       action=f"Execute {t['test_id']} ({t['category']})")
                for t in cov.get("untested", []) if t.get("priority") in ("critical", "high")]
    actions += [Action(priority="P2", owner_area="Test Eng",
                       action=f"Record the result for {t['test_id']} in Jira or reopen it") for t in ambiguous]

    summary = ("Rule-based readout (no LLM). "
               + ("Critical items are failing or unverified - not release-ready."
                  if crit else
                  "No critical items outstanding, but close the coverage and tracking gaps before sign-off."
                  if gate == "conditional-go" else
                  "All planned tests are passing and tracked."))

    headline = (f"{cov['coverage_pct']}% coverage, {cov['pass_rate_pct']}% pass rate; "
                f"{len(crit)} critical item(s) not passing.")
    return Readout(headline=headline, gate_recommendation=gate,
                   confidence=cov.get("confidence", "low"),
                   confidence_rationale=cov.get("confidence_rationale", ""),
                   summary=summary, top_risks=risks[:4],
                   tracking_hygiene=hygiene, actions=actions)


def write_report(cov: dict, r: Readout, path: str) -> None:
    """Write the readout as a shareable Markdown gate-review report."""
    md = [f"# Post-Silicon Validation Readout\n",
          f"**{r.headline}**\n",
          f"- **Gate recommendation:** {r.gate_recommendation.upper()}",
          f"- **Confidence:** {r.confidence.upper()} — {r.confidence_rationale}",
          f"- **Coverage:** {cov['coverage_pct']}%  |  **Pass rate:** {cov['pass_rate_pct']}%  "
          f"|  **Critical items not passing:** {len(cov['critical_gaps'])}\n",
          f"{r.summary}\n",
          "## Top risks"]
    md += [f"- {x}" for x in r.top_risks]
    if r.tracking_hygiene:
        md += ["\n## Tracking hygiene (Jira not 100%)"]
        md += [f"- ⚠ {x}" for x in r.tracking_hygiene]
    md += ["\n## Prioritized actions",
           "| Priority | Owner | Action |", "|---|---|---|"]
    md += [f"| {a.priority} | {a.owner_area} | {a.action} |"
           for a in sorted(r.actions, key=lambda x: x.priority)]
    if cov.get("by_category"):
        md += ["\n## Coverage by category",
               "| Category | Passed / Planned | % |", "|---|---|---|"]
        md += [f"| {c} | {v['passed']}/{v['planned']} | {v['pct']}% |"
               for c, v in cov["by_category"].items()]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"\nReport written to {path}")


# --------------------------------------------------------------------------- #
# 3. Presentation
# --------------------------------------------------------------------------- #

def print_readout(cov: dict, r: Readout) -> None:
    bar = "=" * 70
    print(f"\n{bar}\nPOST-SILICON VALIDATION READOUT\n{bar}")
    print(f"\n{r.headline}\n")
    print(f"Gate recommendation: {r.gate_recommendation.upper()}   "
          f"Confidence: {r.confidence.upper()}")
    print(f"  (confidence basis: {r.confidence_rationale})")
    print(f"Coverage: {cov['coverage_pct']}%   Pass rate: {cov['pass_rate_pct']}%   "
          f"Critical items not passing: {len(cov['critical_gaps'])}")
    print(f"\n{r.summary}\n")
    print("Top risks:")
    for risk in r.top_risks:
        print(f"  - {risk}")
    if r.tracking_hygiene:
        print("\nTracking hygiene flags (Jira not 100%):")
        for h in r.tracking_hygiene:
            print(f"  [!] {h}")
    print("\nPrioritized actions:")
    for a in sorted(r.actions, key=lambda x: x.priority):
        print(f"  [{a.priority}] ({a.owner_area}) {a.action}")
    print(f"\n{bar}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Post-silicon validation copilot")
    ap.add_argument("--plan", default="sample_data/validation_plan.csv")
    ap.add_argument("--logs", default="sample_data/bench_logs.csv",
                    help="Bench-log CSV (used when --jira is not supplied)")
    ap.add_argument("--jira", default=None,
                    help="Jira CSV export; when set, reconciles the plan against the "
                         "Jira board instead of bench logs and flags tracking gaps")
    ap.add_argument("--stale-days", type=int, default=14,
                    help="Flag in-flight Jira tickets not updated in this many days (default 14)")
    ap.add_argument("--no-llm", action="store_true",
                    help="Skip Claude; produce a rule-based readout (no API key needed)")
    ap.add_argument("--report", default=None,
                    help="Also write the readout to this Markdown file (e.g. readout.md)")
    args = ap.parse_args()

    plan = load_csv(args.plan)
    if args.jira:
        cov = jira_adapter.reconcile(plan, jira_adapter.load_jira(args.jira),
                                     stale_days=args.stale_days)
        print(f"\n[source: Jira export {args.jira}]")
    else:
        cov = analyze_coverage(plan, load_csv(args.logs))

    cov["by_category"] = coverage_by_category(plan, {t["test_id"] for t in cov.get("passed", [])})
    cov["confidence"], cov["confidence_rationale"] = assess_confidence(cov)
    facts = render_facts(cov)

    print("\n--- Computed facts (deterministic) ---")
    print(facts)

    if args.no_llm:
        readout = rule_based_readout(cov)
    else:
        readout = generate_readout(facts)

    print_readout(cov, readout)
    if args.report:
        write_report(cov, readout, args.report)


if __name__ == "__main__":
    main()
