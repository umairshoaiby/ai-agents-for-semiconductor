"""Adapter that turns a Jira CSV export into the copilot's internal model.

In real validation programs the "what did we test" picture lives in Jira, not a
tidy bench log. But Jira is never 100%: tickets sit in ambiguous states, required
tests never make it onto the board, and orphan tickets drift in. This adapter
normalizes a Jira export into clean per-test results AND surfaces exactly where
Jira can't be trusted — so the copilot can treat tracking hygiene as its own risk.

The validation plan stays the authoritative source of *what must be tested* and
*how critical it is*. Jira only tells us *what state each test is in*.
"""

import csv
import datetime

# Column names match a standard Jira CSV export. Custom fields export with the
# "Custom field (Name)" prefix; adjust these to your board's exact headers.
COL = {
    "key": "Issue key",
    "summary": "Summary",
    "status": "Status",
    "resolution": "Resolution",
    "component": "Component/s",
    "test_id": "Custom field (Test ID)",
    "result": "Custom field (Test Result)",
    "updated": "Updated",
}


def load_jira(path: str) -> list[dict]:
    """Parse a Jira CSV export into normalized records with a derived status."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rec = {
                "jira_key": r.get(COL["key"], "").strip(),
                "test_id": r.get(COL["test_id"], "").strip(),
                "summary": r.get(COL["summary"], "").strip(),
                "jira_status": r.get(COL["status"], "").strip(),
                "resolution": r.get(COL["resolution"], "").strip(),
                "test_result": r.get(COL["result"], "").strip(),
                "component": r.get(COL["component"], "").strip(),
                "updated": r.get(COL["updated"], "").strip(),
            }
            rec["derived_status"] = _derive(rec)
            rows.append(rec)
    return rows


def _derive(r: dict) -> str:
    """Map a messy Jira ticket onto one clean state.

    The key judgment call: a ticket marked 'Done' with NO recorded test result is
    'ambiguous' — closed on the board, but we cannot claim it passed. Treating that
    as a pass is exactly how teams ship on unverified coverage.
    """
    result = r["test_result"].lower()
    status = r["jira_status"].lower()
    resolution = r["resolution"].lower()

    if result == "pass":
        return "pass"
    if result == "fail":
        return "fail"
    if resolution in ("won't do", "wont do", "cancelled", "canceled"):
        return "skip"
    if status == "done":
        return "ambiguous"   # closed, but no result recorded → cannot confirm pass
    return "untested"        # to do / in progress / blocked


def _parse_date(s: str):
    try:
        return datetime.date.fromisoformat(s.strip())
    except (ValueError, AttributeError):
        return None


def reconcile(plan: list[dict], jira: list[dict],
              as_of: datetime.date | None = None, stale_days: int = 14) -> dict:
    """Reconcile the authoritative plan against the Jira board.

    Returns the same coverage buckets the copilot already understands, PLUS a
    data-quality view of where Jira is unreliable (untracked / ambiguous / orphan /
    stale). `stale_days` flags in-flight tickets not updated recently relative to
    `as_of` (defaults to today).
    """
    jira_by_test = {r["test_id"]: r for r in jira if r["test_id"]}
    plan_ids = {t["test_id"] for t in plan}

    passed, failed, untested, ambiguous, skipped, untracked = [], [], [], [], [], []
    buckets = {
        "pass": passed, "fail": failed, "untested": untested,
        "ambiguous": ambiguous, "skip": skipped,
    }

    for test in plan:
        j = jira_by_test.get(test["test_id"])
        if j is None:
            untracked.append(test)          # required test not even on the board
            continue
        merged = {
            **test, **j,
            "notes": f"{j['jira_key']} status={j['jira_status']} "
                     f"result={j['test_result'] or 'none'}",
        }
        buckets[j["derived_status"]].append(merged)

    orphans = [r for r in jira if r["test_id"] and r["test_id"] not in plan_ids]

    # Stale: in-flight tickets nobody has touched in `stale_days`.
    as_of = as_of or datetime.date.today()
    cutoff = as_of - datetime.timedelta(days=stale_days)
    stale = [r for r in untested
             if (d := _parse_date(r.get("updated", ""))) and d <= cutoff]

    total = len(plan)
    run = len(passed) + len(failed)         # ambiguous/untested/untracked are NOT 'run'
    not_passing = failed + untested + ambiguous + untracked

    return {
        "total_planned": total,
        "executed_pass": len(passed),
        "passed": passed,
        "failed": failed,
        "untested": untested,
        "skipped": skipped,
        "ambiguous": ambiguous,
        "untracked_in_jira": untracked,
        "orphan_tickets": orphans,
        "stale_tickets": stale,
        "coverage_pct": round(100 * run / total, 1) if total else 0.0,
        "pass_rate_pct": round(100 * len(passed) / run, 1) if run else 0.0,
        "critical_gaps": [t for t in not_passing if t.get("priority") == "critical"],
    }
