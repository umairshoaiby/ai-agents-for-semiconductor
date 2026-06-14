"""Tools the agents call (Anthropic tool-use).

Two kinds, both routed through one `ToolRunner`:

  * Retrieval tool  — `rag_search` / `get_prior_update`: pull cited evidence from the corpus.
  * Deterministic tools — `compute_rag` / `get_schedule` / `get_open_actions`: the trusted
    numbers from status_core.py. Agents MUST call these for any status or figure; they are
    not allowed to compute RAG or aging themselves.

The runner keeps a numbered **evidence pool** (`[S1]`, `[S2]`, …) of every chunk retrieved
across a run, so the agent can cite by stable id and the (Phase-3) critic can verify each
claim against a real source.
"""

import json

import status_core
from retriever import get_retriever
from schemas import Citation

TOOLS = [
    {
        "name": "rag_search",
        "description": "Search the program corpus (meetings, prior weekly updates, specs, Jira, "
                       "action history) for evidence. Returns numbered sources [S#] you can cite. "
                       "Use date/workstream/source_type filters to focus.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for"},
                "workstream": {"type": "string", "description": "Optional workstream filter"},
                "since": {"type": "string", "description": "Optional YYYY-MM-DD; only sources on/after"},
                "source_type": {"type": "string",
                                "enum": ["meeting", "weekly_update", "spec", "jira", "action"]},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_prior_update",
        "description": "Retrieve prior weekly updates to compare against (for trend analysis). "
                       "Optionally focus on a topic.",
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string", "description": "Optional focus topic"}},
        },
    },
    {
        "name": "compute_rag",
        "description": "Get the DETERMINISTIC RAG status (green/amber/red) for a workstream and the "
                       "inputs that drove it. This is authoritative — adopt it; do not invent a color.",
        "input_schema": {
            "type": "object",
            "properties": {"workstream": {"type": "string"}},
            "required": ["workstream"],
        },
    },
    {
        "name": "get_schedule",
        "description": "Get milestones with baseline vs forecast and computed slip days.",
        "input_schema": {
            "type": "object",
            "properties": {"workstream": {"type": "string", "description": "Optional filter"}},
        },
    },
    {
        "name": "get_open_actions",
        "description": "Get open action items with deterministic aging and blocked/slipped state.",
        "input_schema": {
            "type": "object",
            "properties": {"workstream": {"type": "string", "description": "Optional filter"}},
        },
    },
]


class ToolRunner:
    def __init__(self):
        self.retriever = get_retriever()
        self.evidence: list[Citation] = []        # ordered pool; index+1 == [S#]
        self._seen: dict[str, int] = {}           # chunk_id -> S number
        self.calls: list[tuple] = []              # [(tool_name, input), ...] for the trace

    def _cite(self, c: Citation) -> int:
        if c.chunk_id not in self._seen:
            self.evidence.append(c)
            self._seen[c.chunk_id] = len(self.evidence)
        return self._seen[c.chunk_id]

    def _format_hits(self, cites: list[Citation]) -> str:
        if not cites:
            return "No matching sources found."
        lines = []
        for c in cites:
            n = self._cite(c)
            lines.append(f"[S{n}] ({c.source_type}; {c.label()}) {c.snippet}")
        return "\n".join(lines)

    def run(self, name: str, inp: dict) -> str:
        self.calls.append((name, inp))
        if name == "rag_search":
            cites = self.retriever.search(
                inp["query"], workstream=inp.get("workstream"),
                since=inp.get("since"), source_type=inp.get("source_type"))
            return self._format_hits(cites)

        if name == "get_prior_update":
            q = inp.get("topic") or "weekly program update overall status hot topics"
            cites = self.retriever.search(q, source_type="weekly_update", k=4)
            return self._format_hits(cites)

        if name == "compute_rag":
            return json.dumps(status_core.compute_rag(inp["workstream"]), indent=2)

        if name == "get_schedule":
            return json.dumps(status_core.get_schedule(inp.get("workstream")), indent=2)

        if name == "get_open_actions":
            return json.dumps(status_core.get_open_actions(inp.get("workstream")), indent=2)

        return f"Unknown tool: {name}"

    def evidence_block(self) -> str:
        """The full evidence pool, for a final grounding pass / display."""
        return "\n".join(
            f"[S{i + 1}] ({c.source_type}; {c.label()}) {c.snippet}"
            for i, c in enumerate(self.evidence))
