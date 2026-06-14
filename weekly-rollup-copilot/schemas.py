"""Shared Pydantic schemas for the Weekly Program Roll-Up Copilot.

Two layers live here:

  * Extraction schemas (RawAction, Decision, Signal, MeetingExtraction) - what the
    per-meeting "intelligence" agents pull out of unstructured notes. Every item
    carries a `source_quote` so it can be traced back to the transcript (grounding).

  * Output schemas (Ask, HotTopic, ActionItem, WorkstreamSummary, WeeklyUpdate) -
    the assembled weekly update. The deterministic fields (rag, mentions, age_days,
    overall_rag, confidence ...) are computed in Python and enforced onto the result;
    the LLM only writes the prose (headline, summaries, asks).

Keeping these in one module lets intelligence.py and rollup.py share the exact same
shapes, and lets both the LLM path and the offline rule-based path return an
identical WeeklyUpdate.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, PrivateAttr

STATE = Literal["new", "in_progress", "done", "slipped", "blocked"]
RAG = Literal["green", "amber", "red"]
CONFIDENCE = Literal["high", "medium", "low"]


# --------------------------------------------------------------------------- #
# Extraction layer - grounded signal mined from each meeting
# --------------------------------------------------------------------------- #

class RawAction(BaseModel):
    action: str = Field(description="The concrete action item, as a short imperative")
    owner: str = Field(description="Person or role who owns it; 'unassigned' if none stated")
    due: Optional[str] = Field(default=None, description="Due date if stated (YYYY-MM-DD), else null")
    state: STATE = Field(description="new=just raised, done=completed, slipped=missed its date, "
                                     "blocked=can't proceed, in_progress otherwise")
    workstream: Optional[str] = Field(default=None, description="Which workstream this belongs to, if clear")
    source_quote: str = Field(description="The exact sentence from the notes this was taken from")
    # Provenance: which meeting this came from. Stamped after extraction, not by the LLM.
    _meeting: str = PrivateAttr(default="meeting")


class Decision(BaseModel):
    decision: str = Field(description="The decision that was made")
    rationale: Optional[str] = Field(default=None, description="Why, if stated")
    workstream: Optional[str] = Field(default=None)
    source_quote: str = Field(description="The exact sentence this was taken from")
    _meeting: str = PrivateAttr(default="meeting")


class Signal(BaseModel):
    kind: Literal["working", "not_working", "needs_decision"] = Field(
        description="working=going well, not_working=a problem/slip/risk, "
                    "needs_decision=an open question awaiting a call")
    point: str = Field(description="One crisp sentence capturing the point")
    workstream: Optional[str] = Field(default=None)
    source_quote: str = Field(description="The exact sentence this was taken from")
    _meeting: str = PrivateAttr(default="meeting")


class MeetingExtraction(BaseModel):
    """Everything one agent pulls from a single meeting's notes."""
    actions: list[RawAction] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list,
                              description="Short topic phrases discussed (2-4 words each), "
                                          "used to find themes that recur across meetings")


# --------------------------------------------------------------------------- #
# Output layer - the assembled weekly update
# --------------------------------------------------------------------------- #

class Ask(BaseModel):
    priority: Literal["P0", "P1", "P2"] = Field(description="P0=needs a leadership decision now")
    owner_area: str = Field(description="Who the ask is directed to, e.g. Director, Supply Chain")
    ask: str = Field(description="The specific help or decision needed from leadership")


class HotTopic(BaseModel):
    topic: str
    mentions: int = Field(description="How many separate meetings raised it (computed)")
    why_it_matters: str = ""


class ActionItem(BaseModel):
    action: str
    owner: str
    due: Optional[str] = None
    state: STATE
    age_days: Optional[int] = Field(default=None, description="Days since raised (computed)")
    workstream: Optional[str] = None
    source: str = Field(default="", description="meeting + quote (grounding)")


class WorkstreamSummary(BaseModel):
    workstream: str
    rag: RAG = Field(description="Computed from blockers, slip, staleness; adopted verbatim")
    summary: str = Field(default="", description="LLM prose; 1-2 sentences")
    whats_working: list[str] = Field(default_factory=list)
    whats_not: list[str] = Field(default_factory=list)


class WeeklyUpdate(BaseModel):
    headline: str = Field(description="One line a director reads in 5 seconds")
    overall_rag: RAG
    confidence: CONFIDENCE = Field(description="Adopt the computed DATA CONFIDENCE; do not inflate")
    confidence_rationale: str = ""
    executive_summary: str = Field(description="3-5 sentence narrative of the week")
    workstreams: list[WorkstreamSummary] = Field(default_factory=list)
    hot_topics: list[HotTopic] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    open_actions: list[ActionItem] = Field(default_factory=list)
    schedule_slips: list[str] = Field(default_factory=list)
    asks: list[Ask] = Field(default_factory=list)
    data_hygiene: list[str] = Field(default_factory=list)
    pm_take_prompts: list[str] = Field(
        default_factory=list,
        description="2-4 short prompts inviting the PM to add their own judgment, "
                    "e.g. 'Add your call on whether to hold the gate.'")
