"""Weekly Program Roll-Up Copilot.

Turns a week of scattered program signal - schedule, a carried-over action log, and
the messy notes/transcripts of the week's meetings - into an exec-ready draft update
that says what's working, what isn't, where the issues are, and the hot topics.

The trust split mirrors the post-silicon validation copilot:

  1. intelligence.py    reads the meetings and extracts grounded signal (with quotes).
  2. status_adapter.py  computes every status/number deterministically (RAG, action
                        aging, hot-topic frequency, confidence) - never the LLM.
  3. this file          asks Claude to write the *prose* around those trusted facts:
                        the headline, executive summary, per-workstream narrative, the
                        asks to leadership, and prompts for the PM's own take.

So the AI does the reading and the writing; Python owns the judgment-bearing numbers.
Run with --no-llm for a fully deterministic, no-key draft (schedule + action log only).

Usage:
    python rollup.py --no-llm
    python rollup.py --meetings sample_data/meetings
"""

import argparse
import sys
from typing import Literal

# Windows consoles default to cp1252 and crash on Unicode; force UTF-8 output.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from pydantic import BaseModel, Field

import status_adapter
from config import MODEL, get_client
from schemas import Ask, WeeklyUpdate, WorkstreamSummary


# --------------------------------------------------------------------------- #
# LLM layer - the writer (prose only; it never touches the computed numbers)
# --------------------------------------------------------------------------- #

class _WSNarrative(BaseModel):
    workstream: str
    summary: str = Field(description="1-2 sentence narrative for this workstream")


class Narrative(BaseModel):
    headline: str = Field(description="One line a director reads in 5 seconds")
    executive_summary: str = Field(description="3-5 sentences: the story of the week")
    workstream_summaries: list[_WSNarrative] = Field(default_factory=list)
    asks: list[Ask] = Field(description="What you need from leadership - decisions, help, escalations")
    pm_take_prompts: list[str] = Field(
        description="2-4 short prompts inviting the PM to add their own judgment on top, "
                    "e.g. 'Add your call on whether to hold the EVT gate.'")


WRITER_SYSTEM = (
    "You are a senior hardware program manager writing the weekly program update for "
    "leadership. You are given PRECOMPUTED, trustworthy facts: each workstream's RAG status, "
    "the open action items, recurring hot topics, decisions, schedule slips, and data-quality "
    "gaps. Do NOT recompute or dispute the RAG colors, the numbers, or the action states - they "
    "are authoritative. Your job is the writing: a crisp headline, an honest executive summary, "
    "a one-to-two sentence narrative per workstream, the asks that need leadership attention, "
    "and 2-4 prompts inviting the PM to add their own judgment.\n\n"
    "Be direct and specific - this goes in front of a director. Ground every statement in the "
    "facts; never invent an owner, date, action, or workstream that isn't listed. Treat the "
    "DATA HYGIENE gaps as real risk (a stale or uncovered workstream means the status is "
    "unverified, not necessarily good). Keep the confidence honest: if the facts say confidence "
    "is low, the tone should reflect that the picture is incomplete."
)


def draft_narrative(facts: str) -> Narrative:
    client = get_client()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=2500,
        system=WRITER_SYSTEM,
        messages=[{"role": "user", "content": f"Precomputed weekly facts:\n\n{facts}"}],
        output_format=Narrative,
    )
    return response.parsed_output


# --------------------------------------------------------------------------- #
# Assemble - merge trusted facts with the prose into the final WeeklyUpdate
# --------------------------------------------------------------------------- #

def assemble(facts: dict, narrative: Narrative) -> WeeklyUpdate:
    """The computed fields come from `facts`; only the prose comes from `narrative`."""
    summ_by_ws = {w.workstream: w.summary for w in narrative.workstream_summaries}
    workstreams = [
        WorkstreamSummary(workstream=s.workstream, rag=s.rag,
                          summary=summ_by_ws.get(s.workstream, ""),
                          whats_working=s.whats_working, whats_not=s.whats_not)
        for s in facts["workstreams"]
    ]
    return WeeklyUpdate(
        headline=narrative.headline,
        overall_rag=facts["overall_rag"],
        confidence=facts["confidence"],
        confidence_rationale=facts["confidence_rationale"],
        executive_summary=narrative.executive_summary,
        workstreams=workstreams,
        hot_topics=facts["hot_topics"],
        decisions=facts["decisions"],
        open_actions=facts["open_actions"],
        schedule_slips=facts["schedule_slips"],
        asks=narrative.asks,
        data_hygiene=facts["data_hygiene"],
        pm_take_prompts=narrative.pm_take_prompts,
    )


# --------------------------------------------------------------------------- #
# Offline fallback - same WeeklyUpdate, transparent rules, no API key
# --------------------------------------------------------------------------- #

def rule_based_update(facts: dict) -> WeeklyUpdate:
    """A deterministic weekly update so the tool runs with no API key.

    Same WeeklyUpdate shape as the LLM path - template prose instead of judgment.
    Used for offline/air-gapped runs and as the free path for the eval harness.
    """
    overall = facts["overall_rag"]
    reds = [w.workstream for w in facts["workstreams"] if w.rag == "red"]
    ambers = [w.workstream for w in facts["workstreams"] if w.rag == "amber"]

    workstreams = []
    for s in facts["workstreams"]:
        if s.rag == "green":
            summ = "On track."
        elif s.whats_not:
            summ = "Watch: " + s.whats_not[0]
        else:
            summ = "Amber - needs attention."
        workstreams.append(WorkstreamSummary(
            workstream=s.workstream, rag=s.rag, summary=summ,
            whats_working=s.whats_working, whats_not=s.whats_not))

    headline = (f"Program {overall.upper()}: "
                + (f"{len(reds)} red ({', '.join(reds)})" if reds
                   else f"{len(ambers)} amber ({', '.join(ambers)})" if ambers
                   else "all workstreams green") + ".")
    exec_summary = ("Rule-based draft (no LLM). "
                    + (f"Red on {', '.join(reds)} - "
                       f"{len(facts['schedule_slips'])} schedule slip(s) and "
                       f"{sum(1 for a in facts['open_actions'] if a.state in ('blocked', 'slipped'))} "
                       f"blocked/slipped action(s) need attention." if reds
                       else "No red workstreams; close the amber items and tracking gaps."))

    asks = []
    for a in facts["open_actions"]:
        if a.state == "blocked":
            asks.append(Ask(priority="P0", owner_area=a.owner,
                            ask=f"Unblock: {a.action}"))
    for slip in facts["schedule_slips"][:2]:
        asks.append(Ask(priority="P1", owner_area="Program", ask=f"Confirm recovery plan - {slip}"))

    prompts = ["Add your call on the overall RAG and whether to escalate.",
               "Note any context the data can't show (customer, exec, supplier signals).",
               "Confirm the top 1-2 asks you want leadership to act on."]
    return WeeklyUpdate(
        headline=headline, overall_rag=overall,
        confidence=facts["confidence"], confidence_rationale=facts["confidence_rationale"],
        executive_summary=exec_summary, workstreams=workstreams,
        hot_topics=facts["hot_topics"], decisions=facts["decisions"],
        open_actions=facts["open_actions"], schedule_slips=facts["schedule_slips"],
        asks=asks, data_hygiene=facts["data_hygiene"], pm_take_prompts=prompts)


# --------------------------------------------------------------------------- #
# Markdown export - shareable, paste-into-email/Confluence/Teams
# --------------------------------------------------------------------------- #

RAG_MARK = {"green": "🟢", "amber": "🟡", "red": "🔴"}


def to_markdown(u: WeeklyUpdate, week_of: str | None = None) -> str:
    md = [f"# Weekly Program Update{' — week of ' + week_of if week_of else ''}\n",
          f"**{RAG_MARK[u.overall_rag]} Overall: {u.overall_rag.upper()}**  ·  "
          f"**Confidence: {u.confidence.upper()}** — {u.confidence_rationale}\n",
          f"_{u.headline}_\n",
          "## Executive summary", u.executive_summary, ""]

    md.append("## Workstreams")
    for w in u.workstreams:
        md.append(f"### {RAG_MARK[w.rag]} {w.workstream} — {w.rag.upper()}")
        if w.summary:
            md.append(w.summary)
        if w.whats_working:
            md.append("- **Working:** " + "; ".join(w.whats_working))
        if w.whats_not:
            md.append("- **Not working:** " + "; ".join(w.whats_not))
        md.append("")

    if u.hot_topics:
        md.append("## Hot topics (recurring this week)")
        md += [f"- **{h.topic}** ({h.mentions} meetings) — {h.why_it_matters}" for h in u.hot_topics]
        md.append("")
    if u.decisions:
        md.append("## Decisions")
        md += [f"- {d.decision}" + (f" — _{d.rationale}_" if d.rationale else "") for d in u.decisions]
        md.append("")
    if u.open_actions:
        md += ["## Open actions", "| State | Action | Owner | Due | Age |", "|---|---|---|---|---|"]
        md += [f"| {a.state} | {a.action} | {a.owner} | {a.due or '-'} | "
               f"{str(a.age_days) + 'd' if a.age_days is not None else 'new'} |" for a in u.open_actions]
        md.append("")
    if u.schedule_slips:
        md.append("## Schedule slips")
        md += [f"- {s}" for s in u.schedule_slips]
        md.append("")
    if u.asks:
        md += ["## Asks for leadership", "| Priority | To | Ask |", "|---|---|---|"]
        md += [f"| {a.priority} | {a.owner_area} | {a.ask} |"
               for a in sorted(u.asks, key=lambda x: x.priority)]
        md.append("")
    if u.data_hygiene:
        md.append("## Data hygiene / gaps")
        md += [f"- ⚠ {h}" for h in u.data_hygiene]
        md.append("")
    if u.pm_take_prompts:
        md.append("## Your take (add before sending)")
        md += [f"- [ ] {p}" for p in u.pm_take_prompts]
    return "\n".join(md) + "\n"


# --------------------------------------------------------------------------- #
# Orchestration + CLI
# --------------------------------------------------------------------------- #

def build(workstreams_path, milestones_path, actions_path, meetings_dir=None, use_llm=True):
    """Top-level entry shared by the CLI and the web UI. Returns (facts, WeeklyUpdate)."""
    workstreams = status_adapter.load_csv(workstreams_path)
    milestones = status_adapter.load_csv(milestones_path)
    action_log = status_adapter.load_csv(actions_path)

    mined = None
    if meetings_dir and use_llm:
        import intelligence
        mined = intelligence.mine_meetings(intelligence.load_meetings(meetings_dir))

    facts = status_adapter.fuse(workstreams, milestones, action_log, mined)
    update = assemble(facts, draft_narrative(status_adapter.render_facts(facts))) if use_llm \
        else rule_based_update(facts)
    return facts, update


def print_update(u: WeeklyUpdate) -> None:
    bar = "=" * 72
    print(f"\n{bar}\nWEEKLY PROGRAM UPDATE\n{bar}")
    print(f"\nOverall: {u.overall_rag.upper()}   Confidence: {u.confidence.upper()}"
          f"   ({u.confidence_rationale})")
    print(f"\n{u.headline}\n\n{u.executive_summary}\n")
    print("Workstreams:")
    for w in u.workstreams:
        print(f"  [{w.rag.upper():5}] {w.workstream}: {w.summary}")
        for x in w.whats_working:
            print(f"        + {x}")
        for x in w.whats_not:
            print(f"        - {x}")
    if u.hot_topics:
        print("\nHot topics:")
        for h in u.hot_topics:
            print(f"  * {h.topic} ({h.mentions} meetings) - {h.why_it_matters}")
    if u.open_actions:
        print("\nOpen actions:")
        for a in u.open_actions:
            age = f"{a.age_days}d" if a.age_days is not None else "new"
            print(f"  [{a.state}] {a.action} ({a.owner}, due {a.due or '-'}, {age})")
    if u.schedule_slips:
        print("\nSchedule slips:")
        for s in u.schedule_slips:
            print(f"  - {s}")
    if u.asks:
        print("\nAsks for leadership:")
        for a in sorted(u.asks, key=lambda x: x.priority):
            print(f"  [{a.priority}] ({a.owner_area}) {a.ask}")
    if u.data_hygiene:
        print("\nData hygiene / gaps:")
        for h in u.data_hygiene:
            print(f"  [!] {h}")
    if u.pm_take_prompts:
        print("\nYour take (add before sending):")
        for p in u.pm_take_prompts:
            print(f"  [ ] {p}")
    print(f"\n{bar}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Weekly program roll-up copilot")
    ap.add_argument("--workstreams", default="sample_data/workstreams.csv")
    ap.add_argument("--milestones", default="sample_data/milestones.csv")
    ap.add_argument("--actions", default="sample_data/action_log.csv")
    ap.add_argument("--meetings", default=None,
                    help="Folder of meeting .txt notes to mine (requires API key)")
    ap.add_argument("--no-llm", action="store_true",
                    help="Skip Claude; deterministic schedule+action draft (no key, ignores --meetings)")
    ap.add_argument("--report", default=None, help="Also write the update to this Markdown file")
    args = ap.parse_args()

    facts, update = build(args.workstreams, args.milestones, args.actions,
                          meetings_dir=args.meetings, use_llm=not args.no_llm)

    print("\n--- Computed facts (deterministic) ---")
    print(status_adapter.render_facts(facts))
    print_update(update)

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(to_markdown(update))
        print(f"Report written to {args.report}")


if __name__ == "__main__":
    main()
