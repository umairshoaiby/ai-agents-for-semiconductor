"""Shared data shapes for the RAG layer.

Plain dataclasses (not Pydantic) — these are internal retrieval structures, not LLM
output contracts, so they stay dependency-light. The grounding contract lives in
`Citation`: every retrieved snippet carries the source title, date, and type, so any
claim built on it can be traced back to a real document.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Document:
    """A source document before chunking."""
    doc_id: str
    text: str
    source_type: str                       # meeting | weekly_update | spec | jira | action
    title: str
    date: Optional[str] = None             # YYYY-MM-DD when known
    workstream: Optional[str] = None


@dataclass
class Chunk:
    """A retrievable unit with provenance metadata."""
    chunk_id: str
    text: str
    source_type: str
    title: str
    date: Optional[str] = None
    workstream: Optional[str] = None

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "Chunk":
        return Chunk(**d)


@dataclass
class Citation:
    """A retrieval hit handed back to callers — the grounding unit.

    `text` is the full chunk (fed to the model and checked by evals); `snippet` is a
    short, single-line excerpt for display.
    """
    chunk_id: str
    title: str
    source_type: str
    date: Optional[str]
    text: str
    snippet: str
    score: float
    workstream: Optional[str] = None

    def label(self) -> str:
        d = f", {self.date}" if self.date else ""
        return f"{self.title}{d}"


@dataclass
class WorkstreamAssessment:
    """One workstream-analyst agent's output. `rag` is overwritten with the deterministic
    value post-hoc, so it can never drift from status_core. The narrative fields carry
    inline [S#] citations into `evidence` (the gathered pool)."""
    workstream: str
    rag: str
    summary: str
    whats_working: list = field(default_factory=list)
    whats_not: list = field(default_factory=list)
    trend: str = ""
    key_risks: list = field(default_factory=list)
    evidence: list = field(default_factory=list)      # list[Citation]
    tool_calls: list = field(default_factory=list)    # [(name, input), ...]


@dataclass
class HotTopic:
    """A theme the risk agent surfaced, with whether it's persistent or newly emerging."""
    topic: str
    status: str                                       # persistent | emerging | resolving
    why: str
    evidence: list = field(default_factory=list)      # list[Citation]


@dataclass
class ProgramWeeklyUpdate:
    """The synthesized weekly update produced by the agent graph. `overall_rag` is the
    deterministic rollup of the workstream RAGs (not an LLM choice)."""
    overall_rag: str
    confidence: str
    confidence_rationale: str
    headline: str
    executive_summary: str
    trend_summary: str
    workstreams: list = field(default_factory=list)   # list[WorkstreamAssessment]
    hot_topics: list = field(default_factory=list)     # list[HotTopic]
    asks: list = field(default_factory=list)           # list[str]
    rejected_claims: list = field(default_factory=list)  # [(workstream, claim, reason)] from critic
