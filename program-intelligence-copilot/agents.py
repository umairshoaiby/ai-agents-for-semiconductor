"""Agents — Phase 2: the Workstream Analyst.

A workstream analyst is a real tool-use agent, not a single extraction call. Given a
workstream, it runs a loop: call tools to gather evidence (retrieval) and the trusted
numbers (deterministic status), reason over what it finds, retrieve more if needed, and
finally call `submit_assessment` to return a structured, cited `WorkstreamAssessment`.

Trust rules baked in:
  * RAG comes from the `compute_rag` tool, and is re-applied deterministically after the
    agent submits — so it can never drift from status_core.
  * Narrative claims must cite retrieved sources as [S#]; the evidence pool is attached so a
    critic (Phase 3) can verify every claim against a real chunk.

Usage:
    python agents.py --workstream "Validation"
    python agents.py --all
"""

import argparse
import json
import sys

import status_core
from config import ANALYST_MODEL, get_client
from schemas import HotTopic, WorkstreamAssessment
from tools import TOOLS, ToolRunner


def _aslist(v) -> list:
    """Coerce a submit field to a clean list of strings (models sometimes return a string)."""
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

SUBMIT_TOOL = {
    "name": "submit_assessment",
    "description": "Submit your final assessment of the workstream. Call this once you have "
                   "gathered enough evidence. Every point in whats_working / whats_not / trend "
                   "must cite its source(s) as [S#].",
    "input_schema": {
        "type": "object",
        "properties": {
            "workstream": {"type": "string"},
            "rag": {"type": "string", "enum": ["green", "amber", "red"],
                    "description": "Adopt the value from compute_rag exactly."},
            "summary": {"type": "string", "description": "1-2 sentence narrative"},
            "whats_working": {"type": "array", "items": {"type": "string"}},
            "whats_not": {"type": "array", "items": {"type": "string"}},
            "trend": {"type": "string",
                      "description": "How this workstream changed vs prior weeks, with [S#] cites"},
            "key_risks": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["workstream", "rag", "summary", "whats_working", "whats_not", "trend"],
    },
}

ANALYST_SYSTEM = (
    "You are a workstream analyst on a semiconductor program. Assess ONE workstream for this "
    "week's leadership update. Work like an analyst:\n"
    "  1. Call compute_rag to get the authoritative RAG status — adopt it; never decide the color yourself.\n"
    "  2. Call get_schedule and get_open_actions for the concrete slips and open items.\n"
    "  3. Call rag_search to find supporting evidence in meetings/specs/Jira, and get_prior_update "
    "to see how things changed week-over-week (the trend).\n"
    "  4. Ground every narrative point in retrieved sources, citing them as [S#]. Do not state "
    "anything you can't cite. If evidence is thin, say so.\n"
    "When done, call submit_assessment. Be concise and specific; this goes to a director."
)


def run_workstream_analyst(workstream: str, model: str | None = None,
                           max_steps: int = 8, verbose: bool = False) -> WorkstreamAssessment:
    # Analysts run a multi-tool loop AND emit structured output — that combination is
    # unreliable on the small model, so default to the capable one.
    client = get_client()
    runner = ToolRunner()
    tools = TOOLS + [SUBMIT_TOOL]
    messages = [{"role": "user", "content":
                 f"Assess the '{workstream}' workstream for this week's program update "
                 f"(today is the gate-review week). Gather evidence and the trend, then submit."}]

    for _ in range(max_steps):
        resp = client.messages.create(
            model=model or ANALYST_MODEL, max_tokens=1600,
            system=ANALYST_SYSTEM, tools=tools, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            break

        results, submitted = [], None
        for tu in tool_uses:
            if tu.name == "submit_assessment":
                submitted = tu.input
                results.append({"type": "tool_result", "tool_use_id": tu.id,
                                "content": "Assessment received."})
            else:
                out = runner.run(tu.name, tu.input)
                if verbose:
                    print(f"  · {tu.name}({json.dumps(tu.input)})")
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out})
        messages.append({"role": "user", "content": results})

        if submitted is not None:
            return _build(workstream, submitted, runner)

    # Agent never submitted — return a deterministic skeleton so the pipeline still works.
    rag = status_core.compute_rag(workstream).get("rag", "unknown")
    return WorkstreamAssessment(workstream=workstream, rag=rag,
                                summary="(analyst did not submit an assessment)",
                                evidence=runner.evidence, tool_calls=runner.calls)


def _build(workstream, inp, runner) -> WorkstreamAssessment:
    # Determinism guard: the RAG the report uses is the computed one, not the model's claim.
    computed = status_core.compute_rag(workstream).get("rag", inp.get("rag"))
    return WorkstreamAssessment(
        workstream=inp.get("workstream", workstream),
        rag=computed,
        summary=str(inp.get("summary", "")),
        whats_working=_aslist(inp.get("whats_working")),
        whats_not=_aslist(inp.get("whats_not")),
        trend=str(inp.get("trend", "")),
        key_risks=_aslist(inp.get("key_risks")),
        evidence=runner.evidence,
        tool_calls=runner.calls,
    )


# --------------------------------------------------------------------------- #
# Generic tool-use-with-submit loop (used by the risk, critic, synthesizer agents)
# --------------------------------------------------------------------------- #

def run_tool_agent(system, user_content, tools, submit_name,
                   runner=None, model=None, max_steps=8, verbose=False):
    """Run an agent until it calls `submit_name`; return that tool's input dict (or None)."""
    client = get_client()
    messages = [{"role": "user", "content": user_content}]
    for _ in range(max_steps):
        resp = client.messages.create(model=model or ANALYST_MODEL, max_tokens=2000,
                                      system=system, tools=tools, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            return None
        results, submitted = [], None
        for tu in tool_uses:
            if tu.name == submit_name:
                submitted = tu.input
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": "received"})
            else:
                out = runner.run(tu.name, tu.input) if runner else f"(no tool {tu.name})"
                if verbose:
                    print(f"  · {tu.name}({json.dumps(tu.input)})")
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out})
        messages.append({"role": "user", "content": results})
        if submitted is not None:
            return submitted
    return None


# --------------------------------------------------------------------------- #
# Risk / hot-topic agent — cross-week retrieval, persistent vs emerging
# --------------------------------------------------------------------------- #

SUBMIT_HOT_TOPICS = {
    "name": "submit_hot_topics",
    "description": "Submit the program's hot topics. Each must cite its sources as [S#].",
    "input_schema": {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "status": {"type": "string", "enum": ["persistent", "emerging", "resolving"],
                                   "description": "persistent=raised across multiple weeks, "
                                                  "emerging=new this week, resolving=cooling down"},
                        "why": {"type": "string", "description": "Why it matters, with [S#] cites"},
                    },
                    "required": ["topic", "status", "why"],
                },
            }
        },
        "required": ["topics"],
    },
}

RISK_SYSTEM = (
    "You are the program risk & hot-topic analyst. Find the themes that matter most to "
    "leadership RIGHT NOW by searching across ALL weeks of the corpus. Use rag_search and "
    "get_prior_update to see how each theme evolved. Classify each as persistent (raised over "
    "multiple weeks), emerging (new this week), or resolving. Ground every topic in retrieved "
    "sources cited as [S#]. Submit the top 3-5 via submit_hot_topics."
)


def run_risk_agent(model=None, verbose=False):
    runner = ToolRunner()
    inp = run_tool_agent(
        RISK_SYSTEM,
        "Identify the program's hot topics and risks with their week-over-week status, "
        "then submit them.",
        TOOLS + [SUBMIT_HOT_TOPICS], "submit_hot_topics",
        runner=runner, model=model, verbose=verbose) or {}
    topics = [HotTopic(topic=str(t.get("topic", "")), status=str(t.get("status", "")),
                       why=str(t.get("why", "")), evidence=runner.evidence)
              for t in inp.get("topics", [])]
    return topics, runner


# --------------------------------------------------------------------------- #
# Adversarial critic — verify every claim is supported by its cited evidence
# --------------------------------------------------------------------------- #

SUBMIT_VERDICTS = {
    "name": "submit_verdicts",
    "description": "Submit a supported/unsupported verdict for every claim id.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "supported": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "supported"],
                },
            }
        },
        "required": ["verdicts"],
    },
}

CRITIC_SYSTEM = (
    "You are an ADVERSARIAL verifier. For each numbered claim you are given the [S#] sources it "
    "cites plus the full evidence pool. Decide whether the cited sources actually support the "
    "claim. Be strict: if the sources do not clearly state it, mark supported=false. Markers like "
    "[compute_rag], [get_schedule], [get_open_actions] refer to trusted deterministic tools — "
    "treat claims grounded in those as supported. Default to unsupported when in doubt. Return a "
    "verdict for every claim id via submit_verdicts."
)


def _claims_of(a: WorkstreamAssessment):
    claims = []
    for t in a.whats_working:
        claims.append(("whats_working", t))
    for t in a.whats_not:
        claims.append(("whats_not", t))
    if a.trend:
        claims.append(("trend", a.trend))
    for t in a.key_risks:
        claims.append(("key_risks", t))
    return claims


def critique_assessment(a: WorkstreamAssessment, model=None, verbose=False):
    """Return (cleaned_assessment, rejected) where rejected = [(claim_text, reason)]."""
    claims = _claims_of(a)
    if not claims:
        return a, []
    pool = "\n".join(f"[S{i + 1}] ({c.source_type}; {c.label()}) {c.snippet}"
                     for i, c in enumerate(a.evidence)) or "(no evidence gathered)"
    claim_lines = "\n".join(f"{i + 1}. ({kind}) {text}" for i, (kind, text) in enumerate(claims))
    content = (f"EVIDENCE POOL:\n{pool}\n\nCLAIMS about workstream '{a.workstream}':\n{claim_lines}\n\n"
               f"Return a verdict for each claim id.")
    inp = run_tool_agent(CRITIC_SYSTEM, content, [SUBMIT_VERDICTS], "submit_verdicts",
                         runner=None, model=model, verbose=verbose) or {}
    verdicts = {v.get("id"): v for v in inp.get("verdicts", [])}

    kept = {"whats_working": [], "whats_not": [], "key_risks": []}
    trend = a.trend
    rejected = []
    for i, (kind, text) in enumerate(claims):
        v = verdicts.get(i + 1)
        supported = (v is None) or bool(v.get("supported", True))   # missing verdict -> keep
        if supported:
            if kind == "trend":
                pass
            else:
                kept[kind].append(text)
        else:
            reason = (v or {}).get("reason", "not supported by cited sources")
            rejected.append((text, reason))
            if kind == "trend":
                trend = ""
    cleaned = WorkstreamAssessment(
        workstream=a.workstream, rag=a.rag, summary=a.summary,
        whats_working=kept["whats_working"], whats_not=kept["whats_not"],
        trend=trend, key_risks=kept["key_risks"], evidence=a.evidence, tool_calls=a.tool_calls)
    return cleaned, rejected


# --------------------------------------------------------------------------- #
# Synthesizer — compose the leadership update from verified parts
# --------------------------------------------------------------------------- #

SUBMIT_UPDATE = {
    "name": "submit_update",
    "description": "Submit the synthesized weekly update.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {"type": "string", "description": "One line a director reads in 5 seconds"},
            "executive_summary": {"type": "array", "items": {"type": "string"},
                                  "description": "3-5 CONCISE bullet points, each one short sentence "
                                                 "(not a paragraph). Lead with the most important."},
            "trend_summary": {"type": "string", "description": "How the program moved over recent weeks"},
            "asks": {"type": "array", "items": {"type": "string"},
                     "description": "Specific asks for leadership"},
        },
        "required": ["headline", "executive_summary", "trend_summary", "asks"],
    },
}

SYNTH_SYSTEM = (
    "You are the program synthesizer writing the weekly leadership update. You are given the "
    "verified per-workstream assessments (each with its RAG color, summary, what's working / not, "
    "and trend) and the program hot topics. Write a crisp headline, a CONCISE executive summary as "
    "3-5 short bullet points (each one sentence, leading with the most important — not a paragraph), "
    "a short trend summary, and the asks for leadership. Do NOT change any RAG color and do NOT "
    "invent facts — use only what you are given. Be direct; this goes to a director."
)


def run_synthesizer(assessments, hot_topics, model=None, verbose=False) -> dict:
    parts = []
    for a in assessments:
        parts.append(
            f"## {a.workstream} [{a.rag.upper()}]\n{a.summary}\n"
            f"Working: {'; '.join(a.whats_working) or '-'}\n"
            f"Not working: {'; '.join(a.whats_not) or '-'}\n"
            f"Trend: {a.trend or '-'}\nRisks: {'; '.join(a.key_risks) or '-'}")
    topics = "\n".join(f"- {t.topic} ({t.status}): {t.why}" for t in hot_topics) or "(none)"
    content = f"WORKSTREAM ASSESSMENTS:\n\n" + "\n\n".join(parts) + f"\n\nHOT TOPICS:\n{topics}"
    return run_tool_agent(SYNTH_SYSTEM, content, [SUBMIT_UPDATE], "submit_update",
                          runner=None, model=model, verbose=verbose) or {}


def print_assessment(a: WorkstreamAssessment) -> None:
    bar = "=" * 72
    print(f"\n{bar}\nWORKSTREAM ASSESSMENT — {a.workstream}  [{a.rag.upper()}]\n{bar}")
    print(f"\n{a.summary}\n")
    if a.whats_working:
        print("What's working:")
        for x in a.whats_working:
            print(f"  + {x}")
    if a.whats_not:
        print("\nWhat's not:")
        for x in a.whats_not:
            print(f"  - {x}")
    if a.trend:
        print(f"\nTrend: {a.trend}")
    if a.key_risks:
        print("\nKey risks:")
        for x in a.key_risks:
            print(f"  ! {x}")
    if a.evidence:
        print("\nEvidence pool (cited sources):")
        for i, c in enumerate(a.evidence, 1):
            print(f"  [S{i}] {c.label()}  ({c.source_type})")
    print(f"\n{bar}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the workstream-analyst agent")
    ap.add_argument("--workstream", default="Validation")
    ap.add_argument("--all", action="store_true", help="Assess every workstream")
    ap.add_argument("--verbose", action="store_true", help="Print each tool call")
    args = ap.parse_args()

    targets = status_core.list_workstreams() if args.all else [args.workstream]
    for ws in targets:
        print(f"\n>>> analyzing: {ws}")
        a = run_workstream_analyst(ws, verbose=args.verbose)
        print_assessment(a)


if __name__ == "__main__":
    main()
