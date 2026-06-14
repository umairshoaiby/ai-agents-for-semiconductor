"""Trust evaluation harness for the Weekly Roll-Up Copilot.

Each scenario under scenarios/ is a labeled program-week (workstreams, milestones,
action log, and optional meeting notes) plus an expected.json describing the answer
an experienced PM already agreed is correct. The harness runs the real pipeline and
scores it on the same trust dimensions used for the validation copilot, adapted for
this tool:

  determinism            - per-workstream RAG and overall RAG match the labeled answer
  decision_accuracy      - the overall program RAG call is correct
  confidence_calibration - confidence (high/med/low) matches the data completeness
  hygiene_recall         - every injected data-quality gap is surfaced
  grounding              - (LLM only) every extracted quote traces to the real notes
  action_recall          - (LLM only) the action items seeded in meetings are captured
  hot_topic_recall       - (LLM only) the recurring cross-meeting topic ranks #1

Run offline (deterministic checks, no key, free):   python evals/eval_harness.py
Run against live Claude (all checks):                python evals/eval_harness.py --llm
"""

import json
import os
import sys

# Reproducible age/slip math regardless of the real date.
os.environ.setdefault("ROLLUP_TODAY", "2026-06-14")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import rollup            # noqa: E402
import status_adapter    # noqa: E402

SCENARIOS = os.path.join(HERE, "scenarios")

DETERMINISTIC_CHECKS = ["determinism", "decision_accuracy", "confidence_calibration", "hygiene_recall"]
LLM_CHECKS = ["grounding", "action_recall", "hot_topic_recall"]


def _norm(s: str) -> str:
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in s).split())


def evaluate(scenario_dir: str, use_llm: bool) -> tuple[str, dict, list]:
    exp = json.load(open(os.path.join(scenario_dir, "expected.json"), encoding="utf-8"))
    meetings_dir = os.path.join(scenario_dir, "meetings")
    has_meetings = os.path.isdir(meetings_dir)

    facts, update = rollup.build(
        os.path.join(scenario_dir, "workstreams.csv"),
        os.path.join(scenario_dir, "milestones.csv"),
        os.path.join(scenario_dir, "action_log.csv"),
        meetings_dir=meetings_dir if (use_llm and has_meetings) else None,
        use_llm=use_llm)

    rag_by_ws = {w.workstream: w.rag for w in update.workstreams}
    checks = {}

    # 1. determinism - the RAG map is exactly right.
    checks["determinism"] = (
        update.overall_rag == exp["expected_overall_rag"]
        and all(rag_by_ws.get(k) == v for k, v in exp["expected_workstream_rag"].items()))

    # 2. decision_accuracy - the headline program call.
    checks["decision_accuracy"] = update.overall_rag == exp["expected_overall_rag"]

    # 3. confidence_calibration.
    checks["confidence_calibration"] = update.confidence == exp["expected_confidence"]

    # 4. hygiene_recall - every injected gap is named.
    hygiene_text = _norm(" || ".join(update.data_hygiene))
    checks["hygiene_recall"] = all(_norm(s) in hygiene_text
                                   for s in exp.get("expected_hygiene_substrings", []))

    fabricated = []
    if use_llm and has_meetings:
        meeting_text = _norm(" ".join(
            open(os.path.join(meetings_dir, f), encoding="utf-8").read()
            for f in os.listdir(meetings_dir) if f.endswith(".txt")))

        # 5. grounding - every quoted source actually exists in the notes.
        for d in update.decisions:
            if d.source_quote and _norm(d.source_quote) not in meeting_text:
                fabricated.append(f"decision quote not in notes: {d.source_quote[:60]}")
        for a in update.open_actions:
            if ': "' in a.source:
                quote = a.source.split(': "', 1)[1].rstrip('"')
                if _norm(quote) and _norm(quote) not in meeting_text:
                    fabricated.append(f"action quote not in notes: {quote[:60]}")
        checks["grounding"] = not fabricated

        # 6. action_recall - seeded action items are captured.
        action_text = _norm(" || ".join(a.action for a in update.open_actions))
        checks["action_recall"] = all(_norm(s) in action_text
                                      for s in exp.get("expected_action_recall", []))

        # 7. hot_topic_recall - the recurring topic ranks first.
        top = exp.get("expected_hot_topic_top")
        if top:
            checks["hot_topic_recall"] = bool(update.hot_topics) and \
                _norm(top) in _norm(update.hot_topics[0].topic + " " + update.hot_topics[0].why_it_matters)
        else:
            checks["hot_topic_recall"] = True

    return exp.get("name", os.path.basename(scenario_dir)), checks, fabricated


def run_all(use_llm: bool = False) -> list:
    results = []
    for name in sorted(os.listdir(SCENARIOS)):
        path = os.path.join(SCENARIOS, name)
        if os.path.isdir(path):
            sname, checks, fab = evaluate(path, use_llm)
            results.append({"scenario": sname, "checks": checks, "fabricated": fab})
    return results


def main() -> None:
    use_llm = "--llm" in sys.argv
    mode = "LIVE CLAUDE" if use_llm else "OFFLINE (deterministic, rule-based)"
    print(f"\nWeekly Roll-Up Copilot — trust evaluation [{mode}]\n" + "=" * 60)

    results = run_all(use_llm)
    passed = total = 0
    for r in results:
        print(f"\n  Scenario: {r['scenario']}")
        for check, ok in r["checks"].items():
            total += 1
            passed += ok
            print(f"    [{'PASS' if ok else 'FAIL'}] {check}")
        for f in r["fabricated"]:
            print(f"           ! {f}")

    print("\n" + "=" * 60)
    print(f"  {passed}/{total} checks passed across {len(results)} scenarios")
    if not use_llm:
        print("  (offline run scores the deterministic checks; add --llm for extraction recall)")
    print()
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
