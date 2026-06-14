# Evaluation — Weekly Program Roll-Up Copilot

A weekly update that goes in front of leadership has to be **defensible**: the same inputs must
always yield the same status, the status call must be right, the confidence must be honest, and
nothing may be invented or silently dropped. This harness encodes those requirements as scored
checks against hand-labeled scenarios — the same philosophy as the Post-Silicon Validation
Copilot, adapted for unstructured meeting input.

Run it:

```bash
python evals/eval_harness.py          # offline: deterministic checks (no key, free)
python evals/eval_harness.py --llm    # adds the extraction-quality checks against live Claude
```

## The trust dimensions

| Check | Guarantee | How it's proven |
|---|---|---|
| **Determinism** | The RAG colors are repeatable & correct | Every workstream RAG and the overall RAG are computed in plain Python and compared to a hand-labeled answer. |
| **Decision accuracy** | The overall program call is right | The overall green/amber/red is compared to the call an experienced PM agreed is correct. |
| **Confidence calibration** | Confidence is honest, not inflated | Confidence is derived from data completeness (stale updates, uncovered workstreams, unowned/orphan actions) and must match the labeled level. |
| **Hygiene recall** | Every tracking gap is surfaced | Each injected data defect (no owner, orphan workstream, stale update) must appear in the data-hygiene output. |
| **Grounding** *(LLM)* | It never invents an action, decision, or quote | Every extracted action/decision carries its source quote; the harness checks that quote actually appears in the meeting notes. |
| **Action recall** *(LLM)* | It catches the actions raised in meetings | Every action item seeded in the notes must show up in the open-actions list. |
| **Hot-topic recall** *(LLM)* | It surfaces what's actually hot | Topics are ranked by how many separate meetings raised them; the recurring theme must rank first. |

## Scenarios

Each scenario under `evals/scenarios/` is a labeled program week — `workstreams.csv`,
`milestones.csv`, `action_log.csv`, optional `meetings/*.txt`, and an `expected.json` with the
correct answer. Dates are anchored to a fixed "today" (`ROLLUP_TODAY=2026-06-14`) so aging and
slip math are reproducible.

| Scenario | Shape | Expected |
|---|---|---|
| `01_all_green` | Fresh updates, no blockers, no slips, all actions owned | **GREEN**, confidence **high** |
| `02_mixed_amber` | One within-threshold slip; one unowned action | **AMBER**, confidence **medium**, hygiene flags the unowned action |
| `03_red_slip_hot_topic` | Two blockers + large slips, a stale workstream, an orphan action; "audio THD" recurs across both meetings | **RED**, confidence **low**, orphan + stale flagged, "THD" ranks as the #1 hot topic |

## Results

**Offline (deterministic) run: 12 / 12 checks pass across 3 scenarios.**

```
Scenario: all_green            [PASS] determinism  [PASS] decision_accuracy  [PASS] confidence_calibration  [PASS] hygiene_recall
Scenario: mixed_amber          [PASS] determinism  [PASS] decision_accuracy  [PASS] confidence_calibration  [PASS] hygiene_recall
Scenario: red_slip_hot_topic   [PASS] determinism  [PASS] decision_accuracy  [PASS] confidence_calibration  [PASS] hygiene_recall
============================================================
  12/12 checks passed across 3 scenarios
```

**Live run against Claude (`--llm`): 21 / 21 checks pass across 3 scenarios** — the 12 deterministic
checks plus grounding, action recall, and hot-topic recall on each scenario.

The offline run is the canonical, reproducible result: it scores everything that bears a decision,
needs no API key, and is free to run in CI. The `--llm` run additionally exercises the extraction
layer — grounding (no fabricated quotes), action recall, and hot-topic ranking — which depend on a
live model and so are kept separate from the deterministic guarantees.

> **Design note — determinism by construction.** RAG colors are derived *only* from the structured
> inputs (blockers, milestone slip, update staleness), never from the LLM's reading of the meetings.
> That is why the determinism check passes identically offline and live: the same CSVs always
> produce the same status, whether or not Claude was in the loop. Meeting signals enrich the
> narrative (what's working / not), but they cannot move a status color.

## A note on what *isn't* claimed

The harness verifies that the **facts** are correct and grounded. It does not score the *style* of
the narrative prose — that's the LLM's job and is intentionally the only thing left to its
discretion. If the model's wording is off, the numbers, statuses, and source quotes underneath it
are still the auditable, hand-checked ones.
