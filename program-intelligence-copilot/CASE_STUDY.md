# Case Study — Program Intelligence Copilot

## The problem this example exists to answer

Example #2 (`weekly-rollup-copilot`) summarizes one week that fits in a context window. The
obvious next question from a program manager is: *"that's nice, but my program has six months of
history — has this issue slipped before? what did we promise leadership last week? is this risk
new or has it been festering?"* A single-pass summarizer can't answer that. So this example is the
honest next step: **retrieval over the whole program history, and a real multi-agent graph that
decides what to look up, verifies itself, and composes a cited update.**

The point I want a hiring manager to take away isn't "I can use RAG." It's that I know **when**
RAG and multi-agent are warranted (a large, historical corpus you can't trust to one pass) versus
when they're over-engineering (a single week that fits in context, which is exactly why v2 *doesn't*
use them). That judgment is the job.

## Architecture, and why each piece earns its place

```
planner → workstream analysts ×N → risk agent → adversarial critic → synthesizer
            (parallel, tool-use + retrieval)        (rejects ungrounded claims)
```

- **RAG, hybrid.** The corpus (months of meetings, prior updates, action history, specs, Jira) is
  chunked, embedded, and retrieved with **BM25 + optional Voyage vectors** fused by Reciprocal Rank
  Fusion. Hybrid is not a flourish: in a hardware program, exact tokens like `VP-003` and `ATE`
  must survive alongside semantic paraphrase. Pure embeddings would lose them.

- **Tools = the trust boundary.** The deterministic status core (RAG color, action aging, schedule
  slip) is exposed as **tools the agents must call**. An agent can narrate, but it cannot invent a
  number. The overall program RAG is a **deterministic rollup** of the per-workstream colors — never
  an LLM vote.

- **Agents, not one prompt.** A workstream analyst runs a genuine tool-use loop: pull the trusted
  numbers, retrieve evidence, see a slip, retrieve the *history* of that slip, then submit a cited
  assessment. A risk agent searches across weeks to tell **persistent** from **emerging** themes.

- **An adversarial critic.** This is the piece I'm proudest of. Every narrative claim must cite a
  retrieved source; the critic re-reads each claim against its cited evidence and **rejects the ones
  the sources don't actually support** before the synthesizer ever sees them. In a real run it caught
  a *hallucinated action ID* (an action cited that wasn't in the evidence pool), a *source
  misattribution* (a clocking ticket cited as the coupling-bench setup), and *unverifiable figures*
  (a "16-week" number cited to chunks that didn't contain it). The update ships a transparent list of
  what was rejected. That's the difference between a demo and something you'd put in front of a VP.

## The hard engineering calls

- **Windows ARM64 has no `chromadb` / `torch` / `onnxruntime` wheels.** (The same constraint that
  made Example #1 choose Flask over Streamlit.) So instead of a heavyweight vector DB + local
  embedding model, I built a **dependency-light pure-Python + numpy** retriever: BM25 for lexical, a
  tiny numpy cosine store for vectors, and **Voyage over HTTP** for semantic embeddings. It runs with
  **zero keys** in local mode, on any platform. The constraint produced a better, more portable design.

- **Small models can't do multi-tool + structured output together.** I planned to run sub-agents on
  the small/cheap model. In testing it leaked tool-call markup as text and split array fields into
  single characters. The honest lesson — now encoded in `config.py` — is that the capable model is
  required for agents juggling several tools *and* structured submission; the small model is reserved
  for simple single-shot helpers. Knowing where each tier breaks is a real cost/quality skill.

- **Determinism under non-determinism.** Agent prose varies run to run; the numbers must not. So RAG
  is computed by tools and re-applied after submit, the overall rollup is pure Python, and the eval
  harness scores the deterministic spine separately from the (LLM-dependent) grounding behavior.

## How it's proven

`evals/eval_harness.py` scores three suites: **retrieval_recall** (the right evidence surfaces),
**status_determinism** (the tools reproduce the hand-labeled RAG and rollup), and **critic_grounding**
(a planted ungrounded claim is rejected; a supported one is kept). **12/12 offline, 14/14 with the
live critic test.** See [EVALUATION.md](EVALUATION.md).

## What it demonstrates

Hybrid RAG with citations, tool-use agents over a deterministic core, a multi-agent graph with
parallelism, adversarial self-verification, calibrated/deterministic status, platform-aware
engineering, and an evaluation harness — applied to a real semiconductor-NPI program-management
workflow. It is the most advanced example in the portfolio, and deliberately framed as the
*justified* next step beyond the simpler weekly-rollup copilot.
