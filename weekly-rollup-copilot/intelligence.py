"""Meeting & discussion intelligence layer.

This is what makes the rollup understand "the flavor" of the week, not just the
schedule. For each meeting note / transcript it runs one constrained, structured
extraction pass (a focused "agent") that pulls out:

  * action items   - who owes what, and its state
  * decisions      - what got decided, and why
  * signals        - what's working / what's not / what needs a decision
  * topics         - short phrases, used downstream to find cross-meeting hot topics

Every extracted item must carry the exact `source_quote` it came from. That is the
grounding contract: the deterministic layer (status_adapter.py) and the eval harness
both check that nothing was invented - each item traces back to a real sentence.

The LLM only *reads and structures* the messy text here. It does not decide RAG
status, age actions, or rank topics - that is all done deterministically afterward.
"""

import os

from config import MODEL, get_client
from schemas import MeetingExtraction

EXTRACT_SYSTEM = (
    "You are a meticulous program-management analyst reading the raw notes / transcript of "
    "ONE engineering program meeting. Extract only what is actually present in the text. "
    "For every item you return, copy the exact sentence it came from into source_quote - "
    "do not paraphrase the quote, and never invent an action, decision, owner, date, or "
    "test ID that is not in the notes. If something is unclear, leave the optional field null.\n\n"
    "Classify each discussion signal as: 'working' (going well / on track), 'not_working' "
    "(a problem, slip, risk, or failure), or 'needs_decision' (an open question awaiting a "
    "call). Capture action items with their owner and state (new / in_progress / done / "
    "slipped / blocked). For topics, return 2-4 word phrases naming what was discussed "
    "(e.g. 'audio THD', 'ATE program', 'substrate lead time') so recurring themes across "
    "meetings can be detected. Tag each item with its workstream when the notes make it clear."
)


def extract_meeting(name: str, text: str) -> MeetingExtraction:
    """Run the extraction agent over one meeting; stamp each item with the meeting name."""
    client = get_client()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=2500,
        system=EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": f"Meeting: {name}\n\nNotes:\n{text}"}],
        output_format=MeetingExtraction,
    )
    ex = response.parsed_output
    # Stamp provenance so the source tag in the UI / report names the meeting.
    for item in [*ex.actions, *ex.decisions, *ex.signals]:
        item._meeting = name
    return ex


def load_meetings(folder: str) -> dict:
    """Read every .txt note in a folder. Filename (sans extension) is the meeting name."""
    out = {}
    for fn in sorted(os.listdir(folder)):
        if fn.lower().endswith(".txt"):
            with open(os.path.join(folder, fn), encoding="utf-8") as f:
                out[os.path.splitext(fn)[0]] = f.read()
    return out


def mine_meetings(meetings: dict) -> dict:
    """Extract from each meeting and flatten into the shape status_adapter.fuse() expects.

    `meetings` maps meeting-name -> raw text. Returns a dict with combined actions,
    decisions, signals, and a per-meeting list of topics (used for hot-topic ranking).
    """
    actions, decisions, signals, per_meeting_topics = [], [], [], []
    for name, text in meetings.items():
        ex = extract_meeting(name, text)
        actions.extend(ex.actions)
        decisions.extend(ex.decisions)
        signals.extend(ex.signals)
        per_meeting_topics.append(ex.topics)
    return {
        "actions": actions,
        "decisions": decisions,
        "signals": signals,
        "per_meeting_topics": per_meeting_topics,
    }
