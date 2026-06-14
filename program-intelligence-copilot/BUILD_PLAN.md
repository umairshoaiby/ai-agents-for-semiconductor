# Build Plan — Program Intelligence Copilot (Example #3)

> **Status: PLAN for review. No code yet.** This is the design we agreed to before building.
> Packaging: a **new** example, sibling to `weekly-rollup-copilot/` (v1 stays as the clean
> teaching contrast). Embeddings: **config-switchable** local ↔ Voyage.

---

## 1. Why this exists (the honest narrative)

`weekly-rollup-copilot` (v1) reads **one week's meetings directly** because they fit in a context
window. That's the right call for a week — but it means the tool has **no memory** and the "agents"
are really four parallel extraction calls, not agents that *decide what to look up*.

A real program has **months** of history. The questions that actually matter are historical and
cross-cutting: *Has the audio-THD issue slipped before? What did we promise leadership last week?
Is this risk emerging or persistent?* Answering those needs two things v1 doesn't have:

1. **Retrieval (RAG)** over the whole program corpus — so an agent pulls the few relevant chunks
   out of months of transcripts instead of stuffing everything into context.
2. **Real multi-agent orchestration** — a planner that scopes the work, specialist agents that
   independently gather evidence via tools, a critic that adversarially verifies grounding, and a
   synthesizer that composes — with feedback loops.

**Interview story = progression + judgment:** "v1 reads a week directly because it fits; v2 adds
retrieval + an agent graph because real programs have history you can't trust to a single pass."
Knowing *when* RAG/multi-agent is warranted (vs. over-engineering) is the Lead-AI-PM skill.

---

## 2. Architecture

```
CORPUS  (months of meetings · prior weekly updates · action-log history · specs · Jira/email)
   │  ingest → chunk → embed → Vector store (Chroma, on-disk) + BM25 keyword index   [hybrid]
   ▼
ORCHESTRATOR / PLANNER agent            ← discovers workstreams from data at runtime, scopes tasks
   ├─ Workstream Analyst Agent ×N  (parallel, async)
   │     tools: rag_search() · get_schedule() · get_open_actions() · compute_rag() · get_prior_update()
   │     loop: retrieve → reason → retrieve more → emit WorkstreamAssessment (+ citations + trend)
   ├─ Risk / Hot-Topic Agent           ← cross-workstream + cross-WEEK retrieval; persistent vs emerging
   ├─ CRITIC / VERIFIER agent          ← every claim must cite a real retrieved chunk that supports it;
   │     rejects ungrounded claims and loops the author (adversarial, up to N rounds)
   └─ SYNTHESIZER agent                ← composes the final WeeklyUpdate from verified assessments
                                          (headline, exec summary, trends, asks, pm_take_prompts)
```

**The v1 trust boundary is preserved AND strengthened:**
- The deterministic functions (RAG color, action aging, slip math) become **tools the agents must
  call** — they cannot invent a number. Python still owns every figure.
- Every *narrative* claim must carry a **retrieval citation**; the Critic enforces "no supporting
  source → cut it" **at runtime**, not just in the eval harness.
- Confidence stays deterministic, now also factoring **retrieval coverage** (did we find evidence
  for each workstream this week?).

---

## 3. Components & files

```
program-intelligence-copilot/
├── config.py            # model ids, EMBED_PROVIDER (local|voyage), CHROMA_DIR, top_k, thresholds
├── ingest.py            # build the index: load corpus → chunk → embed → Chroma + BM25 (idempotent)
├── retriever.py         # hybrid_search(): vector + BM25 → reciprocal-rank fusion → (opt) rerank → Citations
├── embeddings.py        # provider switch: sentence-transformers (local) | voyageai (cloud)
├── tools.py             # Anthropic tool SCHEMAS + impls; wraps the deterministic core + retriever
├── agents.py            # planner, workstream-analyst, risk, critic, synthesizer (tool-use loops)
├── orchestrator.py      # async wiring: plan → parallel analysts → risk → critic loop → synth
├── ask.py               # dynamic Q&A: router agent answers free-text questions w/ citations (CLI)
├── status_core.py       # reuse v1 deterministic RAG/aging/confidence (vendored from weekly-rollup)
├── schemas.py           # Citation, WorkstreamAssessment, WeeklyUpdate(+trend,+citations), PlannerPlan
├── app.py               # Flask UI: Input · Weekly Update · Ask the Copilot · Trust & Evaluation
├── corpus/              # MULTI-WEEK synthetic program (the demo data)
│   ├── workstreams.csv  milestones.csv  action_log_history.csv  jira_export.csv
│   ├── meetings/        # ~3 weeks × ~3 meetings = ~9 dated .txt transcripts
│   ├── weekly_updates/  # prior weekly updates (.md) so trends can be retrieved
│   └── specs/           # 1-2 short design/spec .md docs
├── index/               # persisted Chroma + BM25 (gitignored, rebuilt by ingest.py)
├── evals/
│   ├── eval_harness.py  # extended checks (below)
│   └── gold/            # labeled questions → expected citations + expected RAG/trend answers
├── README.md  CASE_STUDY.md  EVALUATION.md  requirements.txt  .env.example  .gitignore
```

### 3.1 Corpus & ingestion (`ingest.py`)
- **Loaders** per source type → normalize to `Document{id, text, metadata:{source_type, title, date, workstream?}}`.
- **Chunking:** paragraph/turn-based with overlap; meetings by speaker turn, specs by section — metadata preserved so citations name the meeting + date.
- **Embed** via `embeddings.py` (local or Voyage) → upsert into **Chroma** (persist to `index/`).
- **Keyword index:** `rank_bm25` over the same chunks for hybrid retrieval (so `VP-003`, `ATE` aren't lost to fuzzy vectors).
- Idempotent: content-hash IDs, re-run safe.

### 3.2 Retrieval (`retriever.py`)
- `hybrid_search(query, k, workstream?, since?, source_type?)`:
  vector top-k + BM25 top-k → **reciprocal-rank fusion** → optional local cross-encoder **re-rank** →
  return `Citation{chunk_id, source, date, snippet, score}`.
- Metadata **filters** let a workstream agent retrieve only its lane + cross-cutting docs.

### 3.3 Tools exposed to agents (`tools.py`)
Anthropic tool-use schemas; agents *must* call these rather than guess:
| Tool | Returns | Backed by |
|---|---|---|
| `rag_search(query, workstream?, since?, source_type?)` | cited snippets | retriever.py |
| `get_schedule(workstream?)` | milestones + slip days | deterministic |
| `get_open_actions(workstream?)` | aged/blocked/slipped actions | deterministic |
| `compute_rag(workstream)` | RAG color + the inputs that drove it | deterministic |
| `get_prior_update(weeks_ago)` | a past weekly update | retrieval over `weekly_updates/` |

### 3.4 Agents (`agents.py`, `orchestrator.py`)
- **Planner:** discovers workstreams, emits a `PlannerPlan` (which analysts to spawn, cross-cutting questions).
- **Workstream Analyst ×N (parallel `asyncio`):** tool-use loop → `WorkstreamAssessment{rag(from tool), whats_working[], whats_not[], trend, evidence:Citation[]}`.
- **Risk/Hot-Topic Agent:** cross-week retrieval; ranks **persistent vs emerging** themes by frequency over time.
- **Critic/Verifier (adversarial):** each claim must have a citation whose snippet supports it, and each number must trace to a tool output; loops the author up to N rounds.
- **Synthesizer:** composes final `WeeklyUpdate` (+trend, +citations) from verified parts.
- Deterministic fields enforced in code post-hoc (same discipline as v1).

### 3.5 Dynamic query interface (`ask.py` + UI "Ask the Copilot" tab)
Free-text question → **Router agent** → `rag_search` + deterministic tools → **grounded, cited answer**.
e.g. *"What's the history of the ATE blocker?"* → retrieves across weeks → a cited timeline.
This is the "dynamic" you asked for: the update becomes **queryable**, not a static weekly artifact.

---

## 4. Trust & evaluation (extended harness)

Carries over v1's determinism / decision-accuracy / confidence / hygiene checks, **plus**:

| New check | Guarantee | How proven |
|---|---|---|
| `retrieval_recall` | The right evidence is found | For each gold question, the labeled gold chunk is in top-k |
| `citation_grounding` | No claim without support | Every claim's cited snippet actually supports it (substring + LLM-judge) |
| `no_fabrication` | Numbers/entities are real | Figures match tool outputs; named entities exist in the corpus |
| `trend_accuracy` | Trends are computed, not guessed | Slip/aging trend vs labeled multi-week answer (deterministic) |
| `answer_correctness` | The Q&A is right | Gold question → expected answer fact-check |

Gold set lives in `evals/gold/` (questions + expected citations + expected RAG/trend). Offline checks
(retrieval_recall with local embeddings, trend_accuracy, determinism) run **free, no Claude key**.

---

## 5. Tech stack (revised for Windows ARM64 — IMPORTANT)

During Phase 1 we hit a hard platform constraint: **this machine is Windows ARM64, where
`chromadb` / `torch` / `onnxruntime` have no wheels** (the same reason Example #1 used Flask,
not Streamlit). So the planned Chroma + sentence-transformers stack was replaced with a
**dependency-light, pure-Python + numpy** one — which is honestly a stronger portfolio story:

- **Lexical:** `rank-bm25` (pure Python). **Vectors:** a tiny numpy cosine store (no vector DB).
- **Embeddings:** **Voyage AI** (`voyage-3`) over HTTP — no native deps; or **local = BM25-only**
  (no key, runs anywhere). Switched via `EMBED_PROVIDER` in `config.py`.
- **Fusion:** Reciprocal Rank Fusion across BM25 + vectors (no score calibration needed).
- `anthropic` tool-use; `asyncio` for parallel sub-agents.
- **Model routing for cost:** sub-agents on `claude-haiku-4-5`; critic + synthesizer on `claude-opus-4-8`. Overridable in `config.py`.
- Flask UI extended with the **Ask** tab and inline citation chips.

> Net effect: the repo installs and runs with **zero keys** in local mode, on any platform,
> with no heavyweight ML dependencies — and adds semantic retrieval the moment a Voyage key is set.

### Phase 1 status: ✅ COMPLETE
`config.py · embeddings.py · schemas.py · ingest.py · retriever.py · ask.py · evals/` +
a ~3-week synthetic `corpus/`. Retrieval eval **7/7 offline**; live cited Q&A verified
(reconstructs multi-week trends with per-claim citations).

### Phase 3 status: ✅ COMPLETE
`orchestrator.py` runs planner → parallel workstream analysts → risk/hot-topic agent →
adversarial critic ×N → synthesizer. `agents.py` adds `run_risk_agent`, `critique_assessment`,
`run_synthesizer`, and a generic `run_tool_agent` loop; `schemas.py` adds `HotTopic` and
`ProgramWeeklyUpdate`. Verified live end-to-end: overall RAG is a deterministic rollup, the
critic rejected 7 ungrounded claims (caught a hallucinated action id, a source misattribution,
and unverifiable figures) and they were stripped before synthesis. Parallelism via threads
(sync SDK). Remaining: Phase 4 (Flask UI + extended evals: citation-grounding, no-fabrication,
trend-accuracy).

### Phase 2 status: ✅ COMPLETE
`status_core.py` (deterministic RAG/aging/schedule) exposed as **tools** (`tools.py`:
compute_rag, get_schedule, get_open_actions, rag_search, get_prior_update) + the
**workstream-analyst agent** (`agents.py`) running a tool-use loop into a structured,
cited `WorkstreamAssessment`. Verified live: the analyst calls compute_rag (RAG re-applied
deterministically post-submit so it can't drift), cites every claim `[S#]`, and reconstructs
the multi-week trend. Note: the analyst uses the capable model (opus) — the small model was
unreliable at multi-tool + structured submit together.

## 6. Phased milestones (each independently runnable)
1. **RAG foundation** — corpus + `ingest.py` + hybrid `retriever.py` + citations + `ask.py` (single retrieval-augmented Q&A). Provable via `retrieval_recall`. *No agents yet.*
2. **Tools + one analyst** — wrap deterministic core as tools; one Workstream Analyst doing tool-use + retrieval → cited assessment.
3. **Full orchestration** — planner + parallel analysts + risk agent + critic loop + synthesizer → full `WeeklyUpdate` with trends + citations.
4. **UI + Ask tab + extended evals + docs** (README/CASE_STUDY/EVALUATION) + umbrella README row.

## 7. Risks & tradeoffs (called out up front)
- **Cost/latency:** many agent calls → mitigate with parallelism, haiku for sub-agents, caching of retrieval.
- **Determinism:** agent *prose* is non-deterministic; *numbers* stay deterministic via tools; evals target grounding + recall, not exact wording.
- **First-run weight:** local embeddings download a ~80–400 MB model once, then fully offline; Voyage avoids the download but needs a key. Both are config-switchable so the repo runs with **zero keys** in local mode.
- **Windows:** chromadb + sentence-transformers (CPU torch) wheels are available; `requirements.txt` will pin CPU torch and note the first-run download.

## 8. Open questions for you
- Corpus size for the demo: **~3 weeks** is enough to show trends without bloating the repo — OK?
- Keep the v1 `WeeklyUpdate` schema as the output (extended with trend + citations), so the two
  examples visibly share DNA — agreed?
