# Program Intelligence Copilot

**A retrieval-augmented, multi-agent program analyst for semiconductor NPI.** Where
[`weekly-rollup-copilot`](../weekly-rollup-copilot) (Example #2) summarizes a *single week*
that fits in a context window, this one reasons over the program's **entire history** —
months of meetings, prior weekly updates, the action-log history, specs, and Jira — using
**RAG** to retrieve evidence and (in later phases) a **multi-agent graph** to compose
trustworthy, cited updates and answer questions dynamically.

> **Build status: complete (Phases 1–4).** RAG foundation + cited Q&A, deterministic tools +
> workstream-analyst agent, the full **multi-agent graph** (planner → parallel analysts → risk
> agent → adversarial critic → synthesizer), and a **Flask web UI** (Ask + Weekly Update + Trust)
> with an extended eval harness (**12/12 offline, 14/14 with the live critic test**).
> See [BUILD_PLAN.md](BUILD_PLAN.md), [CASE_STUDY.md](CASE_STUDY.md), [EVALUATION.md](EVALUATION.md).

## Why it exists

The honest progression: v2 reads one week directly *because it fits*. Real programs have
**history** — and the questions that matter are historical ("has this slipped before? what
did we promise last week? is this risk emerging or persistent?"). Answering those needs
retrieval over a corpus too big for one prompt, plus agents that decide *what to look up*.
Knowing when RAG / multi-agent is warranted (vs. over-engineering) is the point.

## What works today (Phase 1)

- **Ingest** every source into a provenance-tagged, chunked index (`ingest.py`).
- **Hybrid retrieval** — lexical **BM25** + optional **Voyage** semantic vectors, fused with
  Reciprocal Rank Fusion, with metadata filters (workstream / date / source type). Hybrid
  matters in hardware: exact tokens like `VP-003` and `ATE` must survive alongside paraphrase.
- **Ask the Copilot** (`ask.py`) — grounded, **cited** Q&A. The model sees only retrieved
  chunks, must cite them, and must say when the answer isn't in the sources.
- **Retrieval eval** (`evals/eval_harness.py`) — `retrieval_recall` against a labeled gold set;
  **7/7 offline** on the lexical index, no API key.

## Engineering note: a dependency-light stack (Windows ARM64)

This runs on Windows ARM64, where `chromadb` / `torch` / `onnxruntime` have **no wheels**
(the same constraint that made Example #1 choose Flask over Streamlit). So instead of a heavy
vector DB, retrieval is **pure Python + numpy**: BM25 for lexical, a tiny numpy cosine store
for vectors, and **Voyage AI over HTTP** for semantic embeddings (no native deps). It runs
with **zero keys** in local mode. That's a deliberate platform-aware tradeoff, not a shortcut.

## Quick start

```bash
cd program-intelligence-copilot
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt

python ingest.py                                    # build the index (local/BM25, no key)

# Ask across the whole program history:
python ask.py "how has the channel-B audio THD issue evolved, and is it blocking the gate?"
python ask.py "why is EVT slipping?" 
python ask.py "show me the ATE timeline" --retrieve-only      # offline, no key

python evals/eval_harness.py                        # retrieval_recall: 7/7
```

**Semantic mode (optional):** set `EMBED_PROVIDER=voyage` + `VOYAGE_API_KEY` in `.env`, then
re-run `python ingest.py` — retrieval becomes hybrid BM25 + voyage-3 vectors.

## Phase 2 — tools + the workstream-analyst agent (done)

The deterministic status core ([status_core.py](status_core.py)) is exposed to agents as
**tools** ([tools.py](tools.py)): `compute_rag`, `get_schedule`, `get_open_actions` (trusted
numbers) plus `rag_search` / `get_prior_update` (cited evidence). The **workstream analyst**
([agents.py](agents.py)) runs a tool-use loop, then submits a structured `WorkstreamAssessment`:

```bash
python agents.py --workstream "Validation" --verbose    # see the tool trace
python agents.py --all                                   # assess every workstream
```

Trust guarantees: the agent **must call `compute_rag`** for the color (and it's re-applied
deterministically after submit, so it can't drift), and every narrative point cites a source
`[S#]` or the tool it came from. The agent independently reconstructs the multi-week trend.

## Phase 3 — the multi-agent graph (done)

[orchestrator.py](orchestrator.py) runs the full graph:

```bash
python orchestrator.py            # planner → analysts → risk → critic → synthesizer
python orchestrator.py --verbose  # show every agent's tool calls
```

```
planner (discover workstreams)
  → workstream analysts ×N   (parallel; tool-use + retrieval → cited assessments)
  → risk / hot-topic agent   (cross-week; persistent vs emerging vs resolving)
  → adversarial critic ×N    (rejects any claim its cited sources don't support)
  → synthesizer              (composes the leadership update)
```

What this buys over a single pass:
- **Determinism end to end:** each analyst's RAG comes from `compute_rag`; the **overall RAG is
  a deterministic rollup** of those colors, never an LLM choice.
- **Adversarial grounding:** the critic verifies every narrative claim against its cited sources
  and **strips ungrounded ones before synthesis** — in testing it caught a hallucinated action
  ID, a source misattribution, and unverifiable figures, and the update lists what it rejected.
- **Trend awareness:** the risk agent and analysts reconstruct how each issue moved week-over-week.

## Phase 4 — web UI + extended evals (done)

```bash
python app.py        # → http://127.0.0.1:5001
```
- **Ask the Copilot** tab — fast, cited Q&A across the program history.
- **Weekly Update** tab — runs the full agent graph and renders the cited update, including the
  claims the critic rejected.
- **Trust & Evaluation** tab — the trust framework and how to run the evals.

Eval harness ([evals/eval_harness.py](evals/eval_harness.py)) scores three suites — retrieval
recall, status determinism, and live critic grounding: **12/12 offline, 14/14 with `--llm`**.

Sample data is fully synthetic. See [BUILD_PLAN.md](BUILD_PLAN.md) for the architecture.
