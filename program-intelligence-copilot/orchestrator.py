"""Orchestrator — Phase 3: the multi-agent graph.

    planner (discover workstreams)
        → workstream analysts ×N        (parallel, tool-use + retrieval)
        → risk / hot-topic agent          (cross-week retrieval)
        → adversarial critic ×N           (rejects any claim not supported by its sources)
        → synthesizer                     (composes the leadership update)

The deterministic trust boundary is preserved end to end: each analyst's RAG comes from the
compute_rag tool (re-applied after submit), the overall RAG is a deterministic rollup of those
colors (never an LLM choice), and the critic strips any narrative claim its cited sources don't
support before synthesis ever sees it.

Usage:
    python orchestrator.py            # run the full graph, print the weekly update
    python orchestrator.py --sequential --verbose
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor

import status_core
from agents import (critique_assessment, run_risk_agent, run_synthesizer,
                    run_workstream_analyst)
from schemas import ProgramWeeklyUpdate

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


def _rollup(rags) -> str:
    if "red" in rags:
        return "red"
    if "amber" in rags:
        return "amber"
    return "green"


def _confidence(workstreams) -> tuple[str, str]:
    """Confidence reflects data completeness, computed deterministically (not by an agent)."""
    stale = [w for w in workstreams if status_core.compute_rag(w).get("stale_update")]
    if stale:
        return "medium", f"{len(stale)} workstream(s) have a stale status update: {', '.join(stale)}"
    return "high", "all workstreams have a fresh update"


def run_program_update(parallel=True, verbose=False) -> ProgramWeeklyUpdate:
    def log(msg):
        print(msg, flush=True)

    # --- Planner: discover the workstreams to scope the run (deterministic) ---
    workstreams = status_core.list_workstreams()
    log(f"[planner] {len(workstreams)} workstreams: {', '.join(workstreams)}")

    # --- Workstream analysts (parallel) ---
    log("[analysts] gathering evidence + assessing each workstream…")
    if parallel:
        with ThreadPoolExecutor(max_workers=min(4, len(workstreams))) as ex:
            assessments = list(ex.map(
                lambda w: run_workstream_analyst(w, verbose=verbose), workstreams))
    else:
        assessments = [run_workstream_analyst(w, verbose=verbose) for w in workstreams]
    for a in assessments:
        log(f"   - {a.workstream}: {a.rag.upper()} "
            f"({len(a.whats_working)} working / {len(a.whats_not)} issues, {len(a.evidence)} sources)")

    # --- Risk / hot-topic agent (cross-week) ---
    log("[risk] scanning all weeks for persistent vs emerging themes…")
    hot_topics, _ = run_risk_agent(verbose=verbose)
    for t in hot_topics:
        log(f"   - {t.topic} [{t.status}]")

    # --- Adversarial critic (parallel): reject ungrounded claims ---
    log("[critic] verifying every claim against its cited sources…")
    if parallel:
        with ThreadPoolExecutor(max_workers=min(4, len(assessments))) as ex:
            results = list(ex.map(lambda a: critique_assessment(a, verbose=verbose), assessments))
    else:
        results = [critique_assessment(a, verbose=verbose) for a in assessments]
    critiqued = [r[0] for r in results]
    rejected = [(a.workstream, text, reason)
                for a, (_, rej) in zip(assessments, results) for (text, reason) in rej]
    log(f"   - {len(rejected)} claim(s) rejected as ungrounded" if rejected
        else "   - all claims grounded")

    # --- Synthesizer: compose the update from the verified parts ---
    log("[synthesizer] composing the leadership update…")
    s = run_synthesizer(critiqued, hot_topics, verbose=verbose)

    overall = _rollup([a.rag for a in critiqued])
    conf, conf_rat = _confidence(workstreams)
    exec_summary = s.get("executive_summary", [])
    if isinstance(exec_summary, str):                       # tolerate a paragraph
        exec_summary = [x.strip() for x in exec_summary.split("\n") if x.strip()]
    return ProgramWeeklyUpdate(
        overall_rag=overall, confidence=conf, confidence_rationale=conf_rat,
        headline=s.get("headline", ""), executive_summary=exec_summary,
        trend_summary=s.get("trend_summary", ""), workstreams=critiqued,
        hot_topics=hot_topics, asks=s.get("asks", []), rejected_claims=rejected)


def print_update(u: ProgramWeeklyUpdate) -> None:
    bar = "=" * 74
    print(f"\n{bar}\nWEEKLY PROGRAM UPDATE — multi-agent\n{bar}")
    print(f"\nOverall: {u.overall_rag.upper()}   Confidence: {u.confidence.upper()} "
          f"({u.confidence_rationale})")
    print(f"\n{u.headline}\n")
    for b in u.executive_summary:
        print(f"  • {b}")
    print()
    if u.trend_summary:
        print(f"Trend: {u.trend_summary}\n")
    print("Workstreams:")
    for a in u.workstreams:
        print(f"  [{a.rag.upper():5}] {a.workstream}: {a.summary}")
        for x in a.whats_not:
            print(f"        - {x}")
    if u.hot_topics:
        print("\nHot topics:")
        for t in u.hot_topics:
            print(f"  * {t.topic} [{t.status}] — {t.why}")
    if u.asks:
        print("\nAsks for leadership:")
        for x in u.asks:
            print(f"  - {x}")
    if u.rejected_claims:
        print("\nClaims rejected by the critic (ungrounded — kept out of the update):")
        for ws, text, reason in u.rejected_claims:
            print(f"  x [{ws}] {text}  ({reason})")
    print(f"\n{bar}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the multi-agent program update")
    ap.add_argument("--sequential", action="store_true", help="Disable parallel agents")
    ap.add_argument("--verbose", action="store_true", help="Print each agent's tool calls")
    args = ap.parse_args()
    u = run_program_update(parallel=not args.sequential, verbose=args.verbose)
    print_update(u)


if __name__ == "__main__":
    main()
