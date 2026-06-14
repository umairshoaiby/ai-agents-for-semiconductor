<h1 align="center">AI Agents for Semiconductor 🔬🤖</h1>

<p align="center">
  <b>Practical, runnable AI agent applications for semiconductor & hardware NPI workflows —<br/>
  the AI playbook I wish existed when I ran new product introductions.</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/domain-semiconductor-1f2937" alt="domain"/>
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="python"/>
  <img src="https://img.shields.io/badge/LLM-Claude%20%7C%20GPT%20%7C%20Gemini-6d28d9" alt="llm"/>
  <img src="https://img.shields.io/badge/status-active%20build-success" alt="status"/>
</p>

---

## Why this repo exists

Most open-source AI agent demos solve generic problems — chat with a PDF, build a todo app.
Almost none touch the messy, high-stakes work of **bringing silicon to production**: validation
coverage, datasheet specs, tape-out readiness, defect triage.

I spent 14 years in semiconductor product management (post-silicon validation, tape-out →
production on mixed-signal/analog ICs). This repo is what happens when that domain knowledge
meets modern AI agents — each example is a **real workflow**, framed by someone who's actually
shipped chips, and built to run from a clean clone.

## Examples

| # | Example | What it does | AI techniques |
|---|---------|--------------|---------------|
| 1 | **[post-silicon-validation-copilot](./post-silicon-validation-copilot/)** ✅ | Reconciles a validation plan against a Jira board into a gate-review readout — coverage, calibrated confidence, tracking-hygiene flags, and a prioritized action list. Ships a **clickable UI** and a **trustworthy-AI evaluation harness** | Deterministic-core / LLM-judgment split, structured output, calibrated confidence, evaluation harness, rule-based fallback |
| 2 | **datasheet-extraction-agent** | Reads a mixed-signal datasheet (PDF) and extracts specs into clean structured JSON | RAG, structured output, schema validation |
| 3 | **npi-gate-review-copilot** | Checks a project against phase-gate criteria and flags missing readiness items | Tool calling, checklist reasoning |

> _Example 1 is built and runnable today. Examples 2–3 are next in the build sprint; see the roadmap below._

## Roadmap

- [ ] **FMEA / risk assistant** — drafts a Failure Mode & Effects Analysis from a design description
- [ ] **Yield / defect / wafer-data triage assistant** — likely root causes + next actions from defect data
- [ ] **Supplier / BOM risk agent** — flags single-source parts, EOL risk, lead-time exposure

## Design principles

1. **Runnable, not slideware.** Every example works from `clone → install → run` with sample data.
2. **Domain-honest.** Built by someone who's done the work; the framing reflects real NPI reality.
3. **Product-minded.** Each example's README covers the *problem*, *why it matters*, and *tradeoffs* — not just code.
4. **Model-portable.** Swap between Claude, GPT, and Gemini via a single config; no lock-in.

## Getting started

Each example is self-contained. Pick one and follow its README:

```bash
git clone https://github.com/umairshoaiby/ai-agents-for-semiconductor.git
cd ai-agents-for-semiconductor/post-silicon-validation-copilot
# follow that folder's README.md
```

## About the author

**Syed Umair Shoaiby** — Post-Silicon Project Manager @ Cirrus Logic, pivoting into senior AI
product management. [LinkedIn](https://www.linkedin.com/in/syed-umair-shoaiby/)

> ⚠️ All sample data in this repo is **synthetic** and created for demonstration. Nothing here
> contains proprietary or confidential information from any employer.
