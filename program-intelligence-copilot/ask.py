"""Ask the Copilot — retrieval-augmented, cited Q&A over the program corpus.

This is the Phase-1 payoff: instead of a static weekly artifact, you can interrogate the
whole program history and get a grounded answer with citations. The model is given ONLY
the retrieved chunks and is required to cite them and to say when the answer isn't in the
sources — so it can't drift off the evidence.

Usage:
    python ask.py "what is the history of the audio THD issue?"
    python ask.py "what is blocking EVT?" --workstream "Silicon Bring-Up"
    python ask.py "show me the audio THD timeline" --retrieve-only      # no key needed
"""

import argparse
import sys

from config import MODEL, TOP_K, get_client
from retriever import get_retriever

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

SYSTEM = (
    "You are a program-management analyst answering questions about a semiconductor program. "
    "Answer ONLY from the numbered SOURCES provided — do not use outside knowledge or guess. "
    "Cite the sources you use inline as [1], [2], etc. If the sources are dated, prefer building "
    "a chronological picture and note how things changed over time. If the answer is not in the "
    "sources, say so plainly and name what's missing. Be concise and specific."
)


def build_context(citations) -> str:
    blocks = []
    for i, c in enumerate(citations, 1):
        blocks.append(f"[{i}] ({c.source_type}; {c.label()})\n{c.text}")
    return "\n\n".join(blocks)


def answer(question: str, citations) -> str:
    client = get_client()
    context = build_context(citations)
    resp = client.messages.create(
        model=MODEL, max_tokens=1200, system=SYSTEM,
        messages=[{"role": "user",
                   "content": f"SOURCES:\n\n{context}\n\nQUESTION: {question}"}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def main() -> None:
    ap = argparse.ArgumentParser(description="Ask the program-intelligence copilot")
    ap.add_argument("question", nargs="+", help="Your question")
    ap.add_argument("--k", type=int, default=TOP_K, help="How many chunks to retrieve")
    ap.add_argument("--workstream", default=None, help="Filter to one workstream")
    ap.add_argument("--since", default=None, help="Only sources on/after this date (YYYY-MM-DD)")
    ap.add_argument("--source-type", default=None,
                    help="Filter: meeting | weekly_update | spec | jira | action")
    ap.add_argument("--retrieve-only", action="store_true",
                    help="Just show retrieved sources (no LLM, no API key needed)")
    args = ap.parse_args()
    question = " ".join(args.question)

    r = get_retriever()
    cites = r.search(question, k=args.k, workstream=args.workstream,
                     since=args.since, source_type=args.source_type)
    print(f"\n[retrieval: {r.mode}; {len(cites)} sources]\n")

    if not cites:
        print("No matching sources found (check filters).")
        return

    if args.retrieve_only:
        for i, c in enumerate(cites, 1):
            print(f"[{i}] ({c.source_type}; {c.label()})  score={c.score}")
            print(f"    {c.snippet}\n")
        return

    print("=" * 72)
    print(answer(question, cites))
    print("=" * 72)
    print("\nSources:")
    for i, c in enumerate(cites, 1):
        print(f"  [{i}] {c.label()}  ({c.source_type})")


if __name__ == "__main__":
    main()
