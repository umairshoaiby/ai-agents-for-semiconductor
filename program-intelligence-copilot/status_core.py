"""Deterministic status core (pure Python — no LLM).

This is the trust anchor the agents are NOT allowed to bypass. RAG colors, action aging,
and schedule slips are computed here from the structured inputs (workstreams / milestones /
action-log CSVs in the corpus) and exposed to the agents as tools. An agent can *narrate*
around these numbers but cannot invent them.

Logic mirrors the weekly-rollup-copilot (Example #2): a workstream's RAG depends only on
blockers, milestone slip, and update staleness — never on the model's reading of meetings.
"""

import csv
import os
from datetime import date

from config import CORPUS_DIR, TODAY

STALE_DAYS = 7
SLIP_DAYS = 10


def _load(name: str) -> list[dict]:
    path = os.path.join(CORPUS_DIR, name)
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _today() -> date:
    return date.fromisoformat(TODAY)


def _parse(d):
    try:
        return date.fromisoformat((d or "").strip())
    except (ValueError, AttributeError):
        return None


def _days_since(d):
    p = _parse(d)
    return (_today() - p).days if p else None


def _slip(baseline, forecast) -> int:
    b, f = _parse(baseline), _parse(forecast)
    return (f - b).days if b and f else 0


# --------------------------------------------------------------------------- #
# Public functions (each backs a tool in tools.py)
# --------------------------------------------------------------------------- #

def list_workstreams() -> list[str]:
    return [w["workstream"] for w in _load("workstreams.csv")]


def milestone_slip(workstream: str) -> int:
    return max((_slip(m.get("baseline_date"), m.get("forecast_date"))
               for m in _load("milestones.csv") if m.get("workstream") == workstream),
              default=0)


def compute_rag(workstream: str) -> dict:
    """Deterministic RAG for one workstream, with the inputs that drove it."""
    row = next((w for w in _load("workstreams.csv") if w["workstream"] == workstream), None)
    if not row:
        return {"workstream": workstream, "rag": "unknown", "error": "no such workstream"}
    slip = milestone_slip(workstream)
    has_blocker = bool((row.get("blockers") or "").strip())
    age = _days_since(row.get("last_updated"))
    stale = age is not None and age > STALE_DAYS

    sev = 0
    if stale or has_blocker or (0 < slip <= SLIP_DAYS):
        sev = 1
    if slip > SLIP_DAYS or (has_blocker and slip > 0):
        sev = 2
    rag = ["green", "amber", "red"][sev]
    return {
        "workstream": workstream, "rag": rag, "milestone_slip_days": slip,
        "has_blocker": has_blocker, "blockers": (row.get("blockers") or "").strip(),
        "stale_update": stale, "last_updated": row.get("last_updated"),
        "percent_complete": row.get("percent_complete"),
        "drivers": _why(rag, slip, has_blocker, stale),
    }


def _why(rag, slip, has_blocker, stale) -> str:
    if rag == "green":
        return "fresh update, no blocker, no material slip"
    bits = []
    if slip > SLIP_DAYS:
        bits.append(f"milestone slip {slip}d (> {SLIP_DAYS}d)")
    elif slip > 0:
        bits.append(f"milestone slip {slip}d")
    if has_blocker:
        bits.append("open blocker")
    if stale:
        bits.append(f"status stale (> {STALE_DAYS}d)")
    return "; ".join(bits)


def get_schedule(workstream: str | None = None) -> list[dict]:
    out = []
    for m in _load("milestones.csv"):
        if workstream and m.get("workstream") != workstream:
            continue
        s = _slip(m.get("baseline_date"), m.get("forecast_date"))
        out.append({"milestone": m["milestone"], "workstream": m.get("workstream"),
                    "baseline": m.get("baseline_date"), "forecast": m.get("forecast_date"),
                    "slip_days": s, "status": m.get("status")})
    return out


def get_open_actions(workstream: str | None = None) -> list[dict]:
    out = []
    for row in _load("action_log_history.csv"):
        if workstream and row.get("workstream") != workstream:
            continue
        state = (row.get("state") or "in_progress").strip()
        if state == "done":
            continue
        due = row.get("due") or None
        if state not in ("done", "slipped") and due:
            d = _parse(due)
            if d and d < _today():
                state = "slipped"
        out.append({"id": row.get("id"), "action": row.get("action"),
                    "owner": row.get("owner") or "unassigned", "due": due, "state": state,
                    "age_days": _days_since(row.get("raised_date")),
                    "workstream": row.get("workstream")})
    order = {"blocked": 0, "slipped": 1, "in_progress": 2}
    out.sort(key=lambda a: (order.get(a["state"], 3), -(a["age_days"] or 0)))
    return out
