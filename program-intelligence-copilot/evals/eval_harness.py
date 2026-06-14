"""Trust evaluation for the Program Intelligence Copilot.

Three suites, mirroring the trust story:

  retrieval_recall    (offline) — does the right evidence surface in the top-k? A RAG analyst
                       is only as good as its retrieval.
  status_determinism  (offline) — do the deterministic tools reproduce the hand-labeled RAG
                       for every workstream and the overall rollup? The agents adopt these.
  critic_grounding    (--llm)   — does the adversarial critic KEEP a supported claim and REJECT
                       a planted ungrounded one? Proves the grounding gate actually works.

Offline suites need no API key and are free for CI. The critic test needs Claude.

    python evals/eval_harness.py            # offline suites
    python evals/eval_harness.py --llm      # + the critic-grounding test
"""

import argparse
import json
import os
import sys

os.environ.setdefault("PIC_TODAY", "2026-06-14")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import status_core                     # noqa: E402
from retriever import get_retriever    # noqa: E402

GOLD = os.path.join(HERE, "gold")


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


# --------------------------------------------------------------------------- #
# Suite 1: retrieval_recall (offline)
# --------------------------------------------------------------------------- #

def suite_retrieval(k: int) -> tuple[int, int]:
    cases = json.load(open(os.path.join(GOLD, "retrieval.json"), encoding="utf-8"))
    r = get_retriever()
    passed = 0
    print(f"\n[retrieval_recall]  ({r.mode}, top-{k})")
    for case in cases:
        cites = r.search(case["question"], k=k)
        sub = _norm(case["expect_substring"])
        hit = next((c for c in cites if sub in _norm(c.text) or sub in _norm(c.title)), None)
        type_ok = (not case.get("expect_source_type")
                   or (hit is not None and hit.source_type == case["expect_source_type"]))
        ok = hit is not None and type_ok
        passed += ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {case['question']}")
    return passed, len(cases)


# --------------------------------------------------------------------------- #
# Suite 2: status_determinism (offline)
# --------------------------------------------------------------------------- #

def suite_determinism() -> tuple[int, int]:
    gold = json.load(open(os.path.join(GOLD, "status.json"), encoding="utf-8"))
    print("\n[status_determinism]")
    passed = total = 0

    for ws, want in gold["expected_workstream_rag"].items():
        got = status_core.compute_rag(ws).get("rag")
        ok = got == want
        passed += ok
        total += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {ws}: {got} (want {want})")

    rags = [status_core.compute_rag(w)["rag"] for w in status_core.list_workstreams()]
    overall = "red" if "red" in rags else "amber" if "amber" in rags else "green"
    ok = overall == gold["expected_overall_rag"]
    passed += ok
    total += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] overall rollup: {overall} (want {gold['expected_overall_rag']})")
    return passed, total


# --------------------------------------------------------------------------- #
# Suite 3: critic_grounding (--llm) — plant a bad claim, prove the critic catches it
# --------------------------------------------------------------------------- #

def suite_critic() -> tuple[int, int]:
    from agents import critique_assessment
    from schemas import Citation, WorkstreamAssessment

    evidence = [Citation(
        chunk_id="t1", title="2026-06-09 validation sync", source_type="meeting", date="2026-06-09",
        text="Power suite regression on A2 passed. Audio channel-B THD is still failing.",
        snippet="Power suite regression on A2 passed. Audio channel-B THD is still failing.",
        score=1.0)]
    supported = "Power suite on A2 is passing [S1]"
    planted = "The audio codec passed all THD tests with margin [S1]"   # contradicts S1
    a = WorkstreamAssessment(
        workstream="Validation", rag="red", summary="test",
        whats_working=[supported], whats_not=[planted], evidence=evidence)

    _, rejected = critique_assessment(a)
    rejected_text = " || ".join(t for t, _ in rejected)
    kept_supported = any(supported in x for x in _kept(a, rejected))

    print("\n[critic_grounding]  (live)")
    p = 0
    c1 = "thd tests" in _norm(rejected_text)
    print(f"  [{'PASS' if c1 else 'FAIL'}] rejects the planted ungrounded claim")
    p += c1
    c2 = kept_supported
    print(f"  [{'PASS' if c2 else 'FAIL'}] keeps the supported claim")
    p += c2
    return p, 2


def _kept(a, rejected):
    rejected_texts = {t for t, _ in rejected}
    return [c for c in (a.whats_working + a.whats_not) if c not in rejected_texts]


# --------------------------------------------------------------------------- #

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--llm", action="store_true", help="Also run the live critic-grounding test")
    args = ap.parse_args()

    print("\nProgram Intelligence Copilot — trust evaluation\n" + "=" * 60)
    passed = total = 0
    for fn in (lambda: suite_retrieval(args.k), suite_determinism):
        p, t = fn()
        passed += p
        total += t
    if args.llm:
        p, t = suite_critic()
        passed += p
        total += t

    print("\n" + "=" * 60)
    print(f"  {passed}/{total} checks passed")
    if not args.llm:
        print("  (offline suites; add --llm for the live critic-grounding test)")
    print()
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
