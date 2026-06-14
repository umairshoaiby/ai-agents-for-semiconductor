# Evaluation — Program Intelligence Copilot

A retrieval-augmented, multi-agent analyst can fail in three distinct ways, so the harness scores
three distinct things. Two suites run offline (no key, free for CI); the third exercises the live
critic.

```bash
python evals/eval_harness.py          # offline: retrieval_recall + status_determinism → 12/12
python evals/eval_harness.py --llm    # + critic_grounding (live)                       → 14/14
```

## The suites

| Suite | Guarantee | How it's proven |
|---|---|---|
| **retrieval_recall** *(offline)* | The right evidence surfaces | For each labeled question, the gold fact must appear in the top-k retrieved chunks. A RAG analyst is only as good as its retrieval. |
| **status_determinism** *(offline)* | The numbers are repeatable & correct | `status_core.compute_rag` must reproduce the hand-labeled RAG for every workstream, and the deterministic rollup must match the labeled overall RAG. The agents adopt these via tools. |
| **critic_grounding** *(live)* | The grounding gate actually works | A synthetic assessment is given one **supported** claim and one **planted ungrounded** claim (it contradicts its cited source). The critic must **reject the planted one and keep the supported one**. |

## Results

```
[retrieval_recall]   (lexical BM25, top-6)        7/7 PASS
[status_determinism]                              5/5 PASS   (4 workstreams + overall rollup)
--------------------------------------------------------------
offline total                                    12/12

[critic_grounding]   (live)                        2/2 PASS
--------------------------------------------------------------
with --llm                                       14/14
```

## What each result means

- **retrieval_recall (7/7)** — questions spanning the whole history (the audio-THD timeline, the
  THD spec, why EVT is slipping, when the ATE risk first appeared, last week's RAG, the gate exit
  criteria, a Jira ticket status) all surface their correct source. Runs on the lexical index with
  no key; set `EMBED_PROVIDER=voyage` to score the hybrid retriever against the same gold set.

- **status_determinism (5/5)** — Silicon RED, Firmware GREEN, Validation RED, Supply/Ops AMBER, and
  the RED overall rollup are all reproduced by pure Python. This is the spine the agents are not
  allowed to override.

- **critic_grounding (2/2)** — proves the adversarial critic isn't decorative: it catches a claim its
  source contradicts and leaves a well-supported claim untouched. In full-pipeline runs the same
  mechanism has caught hallucinated action IDs, source misattributions, and unverifiable figures.

## What is *not* claimed

The harness verifies retrieval, the deterministic numbers, and the grounding gate. It does not score
the literary quality of the synthesized prose — that is the one thing left to the model's discretion,
and it sits on top of numbers, statuses, and citations that are all independently checked.

## Reproducibility

Date-dependent math (aging, slips) is anchored with `PIC_TODAY=2026-06-14` (set automatically in the
harness) so results are stable regardless of the real date. All corpus data is synthetic.
