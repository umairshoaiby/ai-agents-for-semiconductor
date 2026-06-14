"""Deterministic program core for the Weekly Roll-Up Copilot.

Everything in this file is plain Python - no LLM. It parses the structured inputs
(workstreams, milestones, carried-over action log), derives each workstream's RAG
status, ages and reconciles action items, ranks cross-meeting hot topics, and rolls
the whole thing up into a single trustworthy `facts` dict.

The intelligence agents (intelligence.py) read the messy meeting notes; this module
owns every number and status so they are auditable and reproducible. The eval
harness scores exactly these computed values.
"""

import csv
from collections import Counter, defaultdict
from datetime import date

from config import HOT_TOPIC_MIN_MENTIONS, SLIP_DAYS, STALE_DAYS, TODAY
from schemas import ActionItem, Decision, HotTopic, WorkstreamSummary


# --------------------------------------------------------------------------- #
# Parsing + small date helpers
# --------------------------------------------------------------------------- #

def load_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _today() -> date:
    return date.fromisoformat(TODAY)


def _parse(d: str | None) -> date | None:
    if not d:
        return None
    try:
        return date.fromisoformat(d.strip())
    except (ValueError, AttributeError):
        return None


def _days_since(d: str | None) -> int | None:
    parsed = _parse(d)
    return (_today() - parsed).days if parsed else None


def _slip_days(baseline: str | None, forecast: str | None) -> int:
    b, f = _parse(baseline), _parse(forecast)
    return (f - b).days if b and f else 0


def _norm(text: str) -> set:
    """Normalised token set for cheap dedup/matching (no external deps)."""
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split()
            if len(w) > 2}


def _similar(a: str, b: str, thresh: float = 0.5) -> bool:
    ta, tb = _norm(a), _norm(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= thresh


# --------------------------------------------------------------------------- #
# Workstream RAG (the heart of the rollup - pure rules)
# --------------------------------------------------------------------------- #

def _milestone_slip(workstream: str, milestones: list[dict]) -> int:
    slips = [_slip_days(m.get("baseline_date"), m.get("forecast_date"))
             for m in milestones if m.get("workstream") == workstream]
    return max(slips, default=0)


def workstream_rag(ws: dict, milestones: list[dict]) -> tuple[str, int]:
    """Derive a workstream's RAG and its milestone slip, deterministically.

    RAG depends ONLY on structured inputs — blockers, milestone slip, and update
    staleness — never on the LLM's reading of the meetings. That is what makes the
    color repeatable and defensible: the same CSVs always give the same status,
    whether or not Claude was in the loop. Meeting signals enrich the narrative
    (what's working / not) but cannot move the color.

    Severity ladder (green=0, amber=1, red=2):
      * a stale update, an open blocker, or a within-threshold slip -> at least amber.
      * a slip beyond SLIP_DAYS, or a blocker that is already moving a date, -> red.
    """
    slip = _milestone_slip(ws["workstream"], milestones)
    has_blocker = bool(ws.get("blockers", "").strip())
    age = _days_since(ws.get("last_updated"))
    stale = age is not None and age > STALE_DAYS

    sev = 0
    if stale or has_blocker or (0 < slip <= SLIP_DAYS):
        sev = 1
    if slip > SLIP_DAYS or (has_blocker and slip > 0):
        sev = 2
    return ["green", "amber", "red"][sev], slip


def _rollup_rag(rags: list[str]) -> str:
    if "red" in rags:
        return "red"
    if "amber" in rags:
        return "amber"
    return "green"


# --------------------------------------------------------------------------- #
# Action reconciliation + aging
# --------------------------------------------------------------------------- #

def reconcile_actions(action_log: list[dict], extracted: list, ws_names: set) -> tuple[list, list]:
    """Merge the carried-over action log with actions mined from meetings.

    Returns (open_actions, hygiene_flags). Carried-over items are aged from their
    raised_date; brand-new items surfaced in meetings (no close text match to a log
    item) are appended. Past-due, not-done items are marked slipped.
    """
    hygiene = []
    open_actions = []

    for row in action_log:
        state = (row.get("state") or "in_progress").strip()
        due = row.get("due") or None
        age = _days_since(row.get("raised_date"))
        # Past due and not finished -> slipped (deterministic, not a guess).
        if state not in ("done", "slipped") and due:
            d = _parse(due)
            if d and d < _today():
                state = "slipped"
        if row.get("workstream") and row["workstream"] not in ws_names:
            hygiene.append(f"Action '{row.get('action', '')[:50]}' is tagged to "
                           f"'{row['workstream']}', which is not a tracked workstream (orphan).")
        if not (row.get("owner") or "").strip():
            hygiene.append(f"Action '{row.get('action', '')[:50]}' has no owner.")
        if state != "done":
            open_actions.append(ActionItem(
                action=row.get("action", ""), owner=row.get("owner") or "unassigned",
                due=due, state=state, age_days=age,
                workstream=row.get("workstream") or None,
                source=f"action_log (raised {row.get('raised_date', '?')})"))

    # New items from meetings that don't match an existing log action.
    log_texts = [r.get("action", "") for r in action_log]
    for a in extracted:
        if any(_similar(a.action, t) for t in log_texts):
            continue
        if any(_similar(a.action, o.action) for o in open_actions):
            continue
        state = a.state
        if state not in ("done", "slipped") and a.due:
            d = _parse(a.due)
            if d and d < _today():
                state = "slipped"
        if not a.owner or a.owner.lower() == "unassigned":
            hygiene.append(f"New action '{a.action[:50]}' was raised with no clear owner.")
        if state != "done":
            open_actions.append(ActionItem(
                action=a.action, owner=a.owner or "unassigned", due=a.due, state=state,
                age_days=None, workstream=a.workstream,
                source=f"{getattr(a, '_meeting', 'meeting')}: \"{a.source_quote[:90]}\""))

    order = {"blocked": 0, "slipped": 1, "new": 2, "in_progress": 3, "done": 4}
    open_actions.sort(key=lambda x: (order.get(x.state, 5), -(x.age_days or 0)))
    return open_actions, hygiene


# --------------------------------------------------------------------------- #
# Hot topics + signal grouping
# --------------------------------------------------------------------------- #

def rank_hot_topics(per_meeting_topics: list[list[str]], signals: list) -> list:
    """A topic is 'hot' when separate meetings raise it (cross-meeting frequency)."""
    canon = {}                       # normalised key -> display label
    meeting_hits = defaultdict(set)  # key -> set(meeting indices)
    for i, topics in enumerate(per_meeting_topics):
        for t in topics:
            key = " ".join(sorted(_norm(t)))
            if not key:
                continue
            canon.setdefault(key, t.strip())
            meeting_hits[key].add(i)

    hot = []
    for key, hits in meeting_hits.items():
        if len(hits) >= HOT_TOPIC_MIN_MENTIONS:
            label = canon[key]
            why = next((s.point for s in signals
                        if _norm(label) & _norm(s.point) and s.kind != "working"),
                       f"Raised in {len(hits)} meetings this week.")
            hot.append(HotTopic(topic=label, mentions=len(hits), why_it_matters=why))
    hot.sort(key=lambda h: -h.mentions)
    return hot


def group_signals(workstream: str, signals: list) -> tuple[list, list]:
    working = [s.point for s in signals if s.workstream == workstream and s.kind == "working"]
    not_working = [s.point for s in signals
                   if s.workstream == workstream and s.kind in ("not_working", "needs_decision")]
    return working, not_working


# --------------------------------------------------------------------------- #
# Confidence (calibrated to data completeness, not to the call)
# --------------------------------------------------------------------------- #

def assess_confidence(facts: dict) -> tuple[str, str]:
    reasons = []
    stale = facts["stale_workstreams"]
    no_signal = facts["uncovered_workstreams"]
    unassigned = sum(1 for a in facts["open_actions"] if a.owner == "unassigned")
    orphan = sum(1 for h in facts["data_hygiene"] if "orphan" in h.lower())

    if stale:
        reasons.append(f"{len(stale)} workstream(s) have a stale status update")
    if no_signal:
        reasons.append(f"{len(no_signal)} workstream(s) had no meeting coverage this week")
    if unassigned:
        reasons.append(f"{unassigned} open action(s) have no owner")
    if orphan:
        reasons.append(f"{orphan} orphan action(s) not tied to a tracked workstream")

    if stale or no_signal:
        level = "low"
    elif unassigned or orphan:
        level = "medium"
    else:
        level = "high"
    return level, "; ".join(reasons) or "all workstreams have a fresh update and meeting coverage"


# --------------------------------------------------------------------------- #
# Fusion - assemble the single trustworthy facts dict
# --------------------------------------------------------------------------- #

def fuse(workstreams, milestones, action_log, mined=None) -> dict:
    """Combine structured inputs + (optional) mined meeting signal into facts.

    `mined` is the intelligence.py output: dict with actions/decisions/signals/topics.
    When it is None (offline / --no-llm), the rollup runs on schedule + action log only.
    """
    meetings_provided = mined is not None
    mined = mined or {}
    signals = mined.get("signals", [])
    extracted_actions = mined.get("actions", [])
    decisions = mined.get("decisions", [])
    per_meeting_topics = mined.get("per_meeting_topics", [])

    ws_names = {w["workstream"] for w in workstreams}
    # The LLM tags items with a loose workstream name ("Silicon" vs "Silicon Bring-Up").
    # Resolve each to the canonical name by shared tokens so coverage/grouping is robust.
    canon = list(ws_names)

    def resolve(name):
        if not name:
            return None
        toks = _norm(name)
        for c in canon:
            if toks & _norm(c):
                return c
        return None

    for item in [*signals, *extracted_actions]:
        if getattr(item, "workstream", None):
            item.workstream = resolve(item.workstream) or item.workstream

    summaries, stale_ws, uncovered = [], [], []
    for w in workstreams:
        working, not_working = group_signals(w["workstream"], signals)
        rag, slip = workstream_rag(w, milestones)
        # Seed what's working / not from the structured row when meetings are silent.
        if not working and not not_working:
            if w.get("blockers", "").strip():
                not_working = [f"Blocker: {w['blockers'].strip()}"]
            if int(w.get("percent_complete", 0) or 0) >= 70 and not w.get("blockers", "").strip():
                working = [w.get("status_note", "").strip() or "Tracking to plan"]
            # Only a real gap when meetings WERE mined but this workstream wasn't discussed.
            if meetings_provided:
                uncovered.append(w["workstream"])
        age = _days_since(w.get("last_updated"))
        if age is not None and age > STALE_DAYS:
            stale_ws.append(w["workstream"])
        summaries.append(WorkstreamSummary(
            workstream=w["workstream"], rag=rag,
            summary="", whats_working=working, whats_not=not_working))

    open_actions, hygiene = reconcile_actions(action_log, extracted_actions, ws_names)
    for w in stale_ws:
        hygiene.append(f"Workstream '{w}' has not refreshed its status in over {STALE_DAYS} days.")
    for w in uncovered:
        if w not in stale_ws:
            hygiene.append(f"Workstream '{w}' had no meeting coverage this week (status unverified).")

    schedule_slips = []
    for m in milestones:
        s = _slip_days(m.get("baseline_date"), m.get("forecast_date"))
        if s > 0:
            schedule_slips.append(
                f"{m['milestone']} ({m.get('workstream', '')}) slipped {s}d "
                f"(baseline {m.get('baseline_date')} -> forecast {m.get('forecast_date')}).")

    overall = _rollup_rag([s.rag for s in summaries])
    hot_topics = rank_hot_topics(per_meeting_topics, signals)

    facts = {
        "overall_rag": overall,
        "workstreams": summaries,
        "open_actions": open_actions,
        "decisions": decisions,
        "hot_topics": hot_topics,
        "schedule_slips": schedule_slips,
        "data_hygiene": hygiene,
        "stale_workstreams": stale_ws,
        "uncovered_workstreams": uncovered,
        "milestones": milestones,
    }
    facts["confidence"], facts["confidence_rationale"] = assess_confidence(facts)
    return facts


# --------------------------------------------------------------------------- #
# Render the facts as an LLM-friendly brief (the ONLY thing the writer sees)
# --------------------------------------------------------------------------- #

def render_facts(facts: dict) -> str:
    def block(title, items):
        body = "\n".join(f"  - {x}" for x in items) or "  (none)"
        return f"{title}\n{body}"

    ws_lines = []
    for s in facts["workstreams"]:
        ws_lines.append(f"  - {s.workstream} [{s.rag.upper()}]")
        for w in s.whats_working:
            ws_lines.append(f"      + working: {w}")
        for n in s.whats_not:
            ws_lines.append(f"      - issue:   {n}")
    ws_block = "\n".join(ws_lines) or "  (none)"

    actions = [f"[{a.state}] {a.action} (owner={a.owner}, due={a.due or '-'}, "
               f"age={a.age_days if a.age_days is not None else '-'}d)"
               for a in facts["open_actions"]]
    decisions = [f"{d.decision}" + (f" — {d.rationale}" if d.rationale else "")
                 for d in facts["decisions"]]
    hot = [f"{h.topic} (raised in {h.mentions} meetings) — {h.why_it_matters}"
           for h in facts["hot_topics"]]

    return f"""OVERALL PROGRAM STATUS: {facts['overall_rag'].upper()}
DATA CONFIDENCE: {facts['confidence']} ({facts['confidence_rationale']})

WORKSTREAMS (RAG computed; adopt verbatim)
{ws_block}

{block("HOT TOPICS (recurring across meetings)", hot)}

{block("DECISIONS MADE", decisions)}

{block("OPEN ACTION ITEMS", actions)}

{block("SCHEDULE SLIPS", facts['schedule_slips'])}

{block("DATA HYGIENE / GAPS", facts['data_hygiene'])}"""
