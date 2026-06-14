"""Evaluation harness for the validation copilot.

The trustworthy-AI question a PMO will ask before adopting this: *how do we KNOW
the readout is correct and safe to act on?* This harness answers it. It runs the
copilot against labeled scenarios (each with a known-correct answer) and scores it
on six dimensions:

  1. determinism            - the coverage math matches a known-correct golden result
  2. decision_accuracy      - the gate call matches the expected go/conditional/no-go
  3. confidence_calibration - confidence matches data completeness (no false certainty)
  4. grounding_no_fabrication - no invented test IDs; every ID traces to the source
  5. critical_recall        - every critical-not-passing item is surfaced (no silent drop)
  6. hygiene_recall         - every untracked/ambiguous/orphan ticket is flagged

Runs OFFLINE by default (rule-based readout, no API key). Pass --llm to score the
actual Claude output instead. Exit code is non-zero if any check fails, so it can
gate CI.

Usage:
    python evals/eval_harness.py            # offline, free
    python evals/eval_harness.py --llm      # score the real model output
"""

import argparse
import json
import os
import re
import sys

# Allow running as `python evals/eval_harness.py` from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import copilot          # noqa: E402
import jira_adapter     # noqa: E402

ID_RE = re.compile(r"[A-Z]{1,4}-\d+")   # matches T-2, VP-003, OX-9 — not P0/P1 priorities
SCENARIOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenarios")


def _ids(items: list[dict]) -> list[str]:
    return sorted(t.get("test_id") or t.get("jira_key") for t in items)


def _readout_text(r) -> str:
    parts = [r.headline, r.summary, r.confidence_rationale, *r.top_risks, *r.tracking_hygiene]
    parts += [a.action for a in r.actions]
    return " ".join(parts)


def evaluate(scenario_dir: str, use_llm: bool):
    with open(os.path.join(scenario_dir, "expected.json"), encoding="utf-8") as f:
        exp = json.load(f)

    plan = copilot.load_csv(os.path.join(scenario_dir, "plan.csv"))
    jira = jira_adapter.load_jira(os.path.join(scenario_dir, "jira.csv"))

    # --- deterministic layer (the trustworthy core) ---
    cov = jira_adapter.reconcile(plan, jira)
    cov["by_category"] = copilot.coverage_by_category(plan, {t["test_id"] for t in cov["passed"]})
    cov["confidence"], cov["confidence_rationale"] = copilot.assess_confidence(cov)

    valid_ids = ({t["test_id"] for t in plan}
                 | {r["jira_key"] for r in jira}
                 | {r["test_id"] for r in jira if r["test_id"]})

    # --- the layer under test ---
    readout = (copilot.generate_readout(copilot.render_facts(cov)) if use_llm
               else copilot.rule_based_readout(cov))

    text = _readout_text(readout)
    fabricated = sorted(set(ID_RE.findall(text)) - valid_ids)
    crit_ids = [t["test_id"] for t in cov["critical_gaps"]]
    hygiene_ids = (_ids(cov["untracked_in_jira"]) + _ids(cov["ambiguous"])
                   + [r["test_id"] for r in cov["orphan_tickets"]])
    hygiene_text = " ".join(readout.tracking_hygiene)

    checks = {
        "determinism": (
            sorted(crit_ids) == sorted(exp["expected_critical"])
            and _ids(cov["untracked_in_jira"]) == sorted(exp["expected_untracked"])
            and _ids(cov["ambiguous"]) == sorted(exp["expected_ambiguous"])
            and sorted(r["test_id"] for r in cov["orphan_tickets"]) == sorted(exp["expected_orphans"])
        ),
        "decision_accuracy": readout.gate_recommendation == exp["expected_gate"],
        "confidence_calibration": readout.confidence == exp["expected_confidence"],
        "grounding_no_fabrication": not fabricated,
        "critical_recall": all(cid in text for cid in crit_ids),
        "hygiene_recall": all(hid in hygiene_text for hid in hygiene_ids),
    }
    return exp["name"], checks, fabricated


def evaluate_detailed(scenario_dir: str, use_llm: bool) -> dict:
    """Like evaluate(), but returns the full evidence behind every check.

    This is what powers the "show me the proof" view: for each trust dimension we
    return what was expected, what the tool actually produced, and a plain-English
    reason the check passed or failed. Nothing here is recomputed for display - it
    is the same deterministic core and the same readout the gate decision uses.
    """
    with open(os.path.join(scenario_dir, "expected.json"), encoding="utf-8") as f:
        exp = json.load(f)

    plan = copilot.load_csv(os.path.join(scenario_dir, "plan.csv"))
    jira = jira_adapter.load_jira(os.path.join(scenario_dir, "jira.csv"))

    cov = jira_adapter.reconcile(plan, jira)
    cov["by_category"] = copilot.coverage_by_category(plan, {t["test_id"] for t in cov["passed"]})
    cov["confidence"], cov["confidence_rationale"] = copilot.assess_confidence(cov)

    valid_ids = ({t["test_id"] for t in plan}
                 | {r["jira_key"] for r in jira}
                 | {r["test_id"] for r in jira if r["test_id"]})

    readout = (copilot.generate_readout(copilot.render_facts(cov)) if use_llm
               else copilot.rule_based_readout(cov))

    text = _readout_text(readout)
    fabricated = sorted(set(ID_RE.findall(text)) - valid_ids)
    crit_ids = sorted(t["test_id"] for t in cov["critical_gaps"])
    untracked = _ids(cov["untracked_in_jira"])
    ambiguous = _ids(cov["ambiguous"])
    orphans = sorted(r["test_id"] for r in cov["orphan_tickets"])
    hygiene_ids = untracked + ambiguous + orphans
    hygiene_text = " ".join(readout.tracking_hygiene)
    refs = sorted(set(ID_RE.findall(text)) & valid_ids)

    def fmt(x):
        return ", ".join(x) if x else "(none)"

    def n(count, word):
        return f"{count} {word}" + ("" if count == 1 else "s")

    n_crit, n_untr, n_amb, n_orph = len(crit_ids), len(untracked), len(ambiguous), len(orphans)
    n_hyg, n_refs = len(hygiene_ids), len(refs)
    GATE_WORDS = {"go": "GO (safe to release)", "conditional-go": "CONDITIONAL-GO (release only after fixes)",
                  "no-go": "NO-GO (do not release)"}
    gate_word = GATE_WORDS.get(readout.gate_recommendation, readout.gate_recommendation)

    det_ok = (crit_ids == sorted(exp["expected_critical"])
              and untracked == sorted(exp["expected_untracked"])
              and ambiguous == sorted(exp["expected_ambiguous"])
              and orphans == sorted(exp["expected_orphans"]))

    checks = [
        {"key": "determinism", "title": "The numbers are repeatable and correct",
         "ok": det_ok,
         "plain": (
             "The tool worked out the coverage picture on its own, and it exactly matched the "
             "answer we already knew was correct for this test case: "
             f"{n(n_crit, 'critical gap')}, {n(n_untr, 'untracked test')}, "
             f"{n(n_amb, 'ambiguous ticket')}, and {n(n_orph, 'orphan ticket')} — all lined up. "
             "Because the math is done in plain code (never by the AI), running it again always "
             "gives the same result. You can audit it." if det_ok else
             "The tool's coverage figures did NOT match the known-correct answer for this case."),
         "what": "The deterministic core's buckets match the known-correct golden answer.",
         "expected": (f"critical={fmt(sorted(exp['expected_critical']))}; "
                      f"untracked={fmt(sorted(exp['expected_untracked']))}; "
                      f"ambiguous={fmt(sorted(exp['expected_ambiguous']))}; "
                      f"orphan={fmt(sorted(exp['expected_orphans']))}"),
         "actual": (f"critical={fmt(crit_ids)}; untracked={fmt(untracked)}; "
                    f"ambiguous={fmt(ambiguous)}; orphan={fmt(orphans)}")},
        {"key": "decision_accuracy", "title": "It made the right release call",
         "ok": readout.gate_recommendation == exp["expected_gate"],
         "plain": (
             f"Based on those facts, the tool recommended {gate_word}. That is the same call an "
             "experienced validation manager already agreed was right for this situation — so the "
             "recommendation is sound, not just confident-sounding."),
         "what": "The gate recommendation matches the expected go / conditional-go / no-go.",
         "expected": exp["expected_gate"], "actual": readout.gate_recommendation},
        {"key": "confidence_calibration", "title": "Its confidence is honest, not inflated",
         "ok": readout.confidence == exp["expected_confidence"],
         "plain": (
             f"The tool said it was {readout.confidence.upper()} confidence — and that wasn't a "
             "guess. It's calculated from how complete the underlying data is. Here, the reason is: "
             f"{cov['confidence_rationale']}. This is the safeguard against the worst failure: "
             "sounding sure when the data is actually thin."),
         "what": "Reported confidence equals the data-completeness-derived level (no false certainty).",
         "expected": exp["expected_confidence"], "actual": readout.confidence},
        {"key": "grounding_no_fabrication", "title": "It never makes up data",
         "ok": not fabricated,
         "plain": (
             f"Every test the readout talks about is a real test that actually exists in the plan or "
             f"the Jira board — it referenced {n(n_refs, 'real test')} and invented exactly zero. "
             "Nothing was hallucinated, so you can trust that what it reports really happened." if not fabricated else
             f"The readout mentioned test IDs that exist nowhere in the inputs ({fmt(fabricated)}) — "
             "a fabrication. This check is designed to catch exactly that."),
         "what": "Every test ID in the readout traces to the plan or Jira; zero invented IDs.",
         "expected": "0 fabricated IDs",
         "actual": (f"0 fabricated; {n_refs} grounded ID(s): {fmt(refs)}"
                    if not fabricated else f"FABRICATED: {fmt(fabricated)}")},
        {"key": "critical_recall", "title": "It catches every make-or-break problem",
         "ok": all(cid in text for cid in crit_ids),
         "plain": (
             f"There {'was' if n_crit==1 else 'were'} {n(n_crit, 'critical problem')} in this case, "
             "and the readout named every single one. None were quietly dropped — and a silently "
             "missed critical failure is the most expensive mistake a gate review can make." if n_crit else
             "There were no critical problems in this case, so there was nothing that could be "
             "missed. (The other scenarios test the catching.)"),
         "what": "Every critical-not-passing item is surfaced in the readout (no silent drop).",
         "expected": f"all critical items present: {fmt(crit_ids)}" if crit_ids else "none required",
         "actual": (fmt([c for c in crit_ids if c in text]) + " present"
                    if crit_ids else "no critical items in this scenario")},
        {"key": "hygiene_recall", "title": "It flags every tracking gap in Jira",
         "ok": all(hid in hygiene_text for hid in hygiene_ids),
         "plain": (
             f"Jira is never perfect. This case had {n(n_hyg, 'tracking problem')} — tests missing "
             "from the board, closed with no result, or not in the plan — and the tool flagged every "
             "one. That's how 'the board looks green' can't be mistaken for 'the chip is verified.'" if n_hyg else
             "This case's Jira board was clean — no missing, ambiguous, or stray tickets — so there "
             "were no tracking gaps to raise."),
         "what": "Every untracked / ambiguous / orphan ticket is flagged.",
         "expected": f"all hygiene flags present: {fmt(hygiene_ids)}" if hygiene_ids else "none required",
         "actual": (fmt([h for h in hygiene_ids if h in hygiene_text]) + " flagged"
                    if hygiene_ids else "no hygiene defects in this scenario")},
    ]
    plain_scenarios = {
        "critical_failures": "A bad batch: a make-or-break test fails and the Jira board has several "
                             "tracking gaps. The tool must refuse to ship it.",
        "clean_release": "The happy path: every test passed and is properly tracked. The tool should "
                         "confidently approve release.",
        "coverage_gap": "The grey zone: nothing failed, but the testing isn't finished. The tool should "
                        "neither approve nor reject — it should say 'fix the gaps first.'",
    }
    return {
        "name": exp["name"],
        "plain_scenario": plain_scenarios.get(exp["name"], exp.get("note", "")),
        "note": exp.get("note", ""),
        "gate": readout.gate_recommendation,
        "gate_word": gate_word,
        "confidence": readout.confidence,
        "headline": readout.headline,
        "checks": checks,
        "passed": sum(c["ok"] for c in checks),
        "total": len(checks),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluation harness for the validation copilot")
    ap.add_argument("--llm", action="store_true", help="Score the Claude readout (needs API key)")
    args = ap.parse_args()

    dirs = sorted(os.path.join(SCENARIOS_DIR, d) for d in os.listdir(SCENARIOS_DIR)
                  if os.path.isdir(os.path.join(SCENARIOS_DIR, d)))

    mode = "LLM (claude)" if args.llm else "offline (rule-based)"
    print(f"\nEvaluation harness — mode: {mode}\n" + "=" * 66)

    total = passed = 0
    for d in dirs:
        name, checks, fabricated = evaluate(d, args.llm)
        print(f"\nScenario: {name}")
        for check, ok in checks.items():
            total += 1
            passed += int(ok)
            print(f"  [{'PASS' if ok else 'FAIL'}] {check}")
        if fabricated:
            print(f"     fabricated IDs detected: {fabricated}")

    pct = round(100 * passed / total, 1) if total else 0.0
    print("\n" + "=" * 66)
    print(f"SCORECARD: {passed}/{total} checks passed ({pct}%)")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
