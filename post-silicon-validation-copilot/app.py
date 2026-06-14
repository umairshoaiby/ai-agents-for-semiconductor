"""Post-Silicon Validation Copilot - clickable web UI (Flask).

A thin Flask front end over the SAME deterministic core and readout engine the CLI
uses (copilot.py / jira_adapter.py). No analysis logic lives here - the UI only
collects inputs, calls the trusted functions, and renders the result. The numbers
on screen are the same auditable numbers the eval harness scores; nothing is
recomputed for display.

Flask is used (not Streamlit) because it is pure-Python and installs on every
platform including Windows ARM64, where pandas/pyarrow wheels are unavailable.

Visual design follows a "data-dense dashboard" system: light enterprise surface,
blue/amber palette, status colors, Fira Code + Fira Sans, KPI donut charts, and
polished sortable-feel tables. Charts are inline SVG (no external library, works
fully offline).

Run it:
    python app.py
then open http://127.0.0.1:5000 in your browser.
"""

import base64
import math
import os
import sys
import tempfile

from flask import Flask, request, render_template_string, redirect

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "evals"))

import copilot              # noqa: E402
import jira_adapter         # noqa: E402

SAMPLE = os.path.join(HERE, "sample_data")
app = Flask(__name__)

# Last-run state, kept in memory so navigating between tabs does not lose your result.
# Cleared by the Reset button or when the server is stopped (closing the app).
LAST_READOUT = None   # render kwargs for the Gate Readout tab
LAST_EVAL = None      # render kwargs for the Trust & Evaluation tab

# Status palette (also used for the donut + gate banner). Green/amber/red.
GATE = {
    "go":             ("#15803d", "#dcfce7", "GO"),
    "conditional-go": ("#b45309", "#fef3c7", "CONDITIONAL-GO"),
    "no-go":          ("#b91c1c", "#fee2e2", "NO-GO"),
}
CONF = {"high": "#15803d", "medium": "#b45309", "low": "#b91c1c"}
P_COLOR = {"P0": "#b91c1c", "P1": "#b45309", "P2": "#475569"}

# The trust framework: the single map that decodes every check. Each scenario result
# links back to a row here, so the technical term, the plain-English guarantee, the
# reason it exists, and the way it's proven all live in one place.
FRAMEWORK = [
    {"key": "determinism", "term": "Determinism",
     "guarantee": "The numbers are repeatable & correct",
     "why": "A gate decision must be auditable. Identical inputs must always give an identical "
            "answer — so a reviewer (or an auditor months later) can reproduce exactly how the "
            "call was reached. If the math drifted run-to-run, you could never defend the decision.",
     "how": "All coverage and data-quality math runs in plain Python — never the AI — and is "
            "compared against a known-correct, hand-labeled answer for each test case."},
    {"key": "decision_accuracy", "term": "Decision accuracy",
     "guarantee": "It makes the right release call",
     "why": "The whole point is the go / conditional-go / no-go recommendation. If that call is "
            "wrong, nothing else matters — you either ship a bad part or block a good one.",
     "how": "The tool's recommendation is compared to the call an experienced validation manager "
            "already agreed is correct for each labeled scenario."},
    {"key": "confidence_calibration", "term": "Confidence calibration",
     "guarantee": "Its confidence is honest, not inflated",
     "why": "The most dangerous AI failure in a gate review is sounding sure on thin data — a "
            "confident 'go' built on half-finished testing. Confidence must reflect reality.",
     "how": "Confidence (high/medium/low) is computed from how complete the data is, and the AI "
            "is forced to adopt it — it cannot talk itself into being more certain than the data allows."},
    {"key": "grounding_no_fabrication", "term": "Grounding",
     "guarantee": "It never makes up data",
     "why": "An AI that invents a passing result or a test ID would launder a false picture into an "
            "authoritative report. Every claim must trace to something real.",
     "how": "Every test ID the readout mentions is matched against the real IDs in the plan and the "
            "Jira export. Any ID that exists nowhere is flagged as a fabrication and fails the check."},
    {"key": "critical_recall", "term": "Critical recall",
     "guarantee": "It catches every make-or-break problem",
     "why": "In a gate decision the expensive error is the missed critical risk, not an extra flag. "
            "A single silently-dropped critical failure can put a broken part into production.",
     "how": "Every critical-priority item that isn't cleanly passing must appear by name in the "
            "readout. If even one is missing, the check fails."},
    {"key": "hygiene_recall", "term": "Hygiene recall",
     "guarantee": "It flags every tracking gap in Jira",
     "why": "Teams act on what the board shows. If a required test is missing, closed with no "
            "result, or a stray ticket drifts in, 'the board is green' can be mistaken for 'the "
            "chip is verified.' Those gaps must be surfaced.",
     "how": "Every untracked, ambiguous, and orphan ticket the deterministic core finds must be "
            "named in the readout's tracking-hygiene section."},
]
FRAMEWORK_MAP = {d["key"]: d for d in FRAMEWORK}


# --------------------------------------------------------------------------- #
# Core wiring - reuse the exact CLI functions
# --------------------------------------------------------------------------- #

def _save_upload(file_storage) -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", mode="wb")
    tmp.write(file_storage.read())
    tmp.close()
    return tmp.name


def build_coverage(plan_path, jira_path, logs_path, stale_days):
    plan = copilot.load_csv(plan_path)
    if jira_path:
        cov = jira_adapter.reconcile(
            plan, jira_adapter.load_jira(jira_path), stale_days=stale_days)
        source = f"Jira export ({os.path.basename(jira_path)})"
    else:
        cov = copilot.analyze_coverage(plan, copilot.load_csv(logs_path))
        source = f"Bench logs ({os.path.basename(logs_path)})"
    cov["by_category"] = copilot.coverage_by_category(
        plan, {t["test_id"] for t in cov.get("passed", [])})
    cov["confidence"], cov["confidence_rationale"] = copilot.assess_confidence(cov)
    return cov, source


def readout_markdown(cov, r) -> str:
    md = [f"# Post-Silicon Validation Readout\n",
          f"**{r.headline}**\n",
          f"- **Gate recommendation:** {r.gate_recommendation.upper()}",
          f"- **Confidence:** {r.confidence.upper()} - {r.confidence_rationale}",
          f"- **Coverage:** {cov['coverage_pct']}%  |  **Pass rate:** {cov['pass_rate_pct']}%  "
          f"|  **Critical items not passing:** {len(cov['critical_gaps'])}\n",
          f"{r.summary}\n", "## Top risks"]
    md += [f"- {x}" for x in r.top_risks]
    if r.tracking_hygiene:
        md += ["\n## Tracking hygiene (Jira not 100%)"] + [f"- {x}" for x in r.tracking_hygiene]
    md += ["\n## Prioritized actions", "| Priority | Owner | Action |", "|---|---|---|"]
    md += [f"| {a.priority} | {a.owner_area} | {a.action} |"
           for a in sorted(r.actions, key=lambda x: x.priority)]
    if cov.get("by_category"):
        md += ["\n## Coverage by category", "| Category | Passed / Planned | % |", "|---|---|---|"]
        md += [f"| {c} | {v['passed']}/{v['planned']} | {v['pct']}% |"
               for c, v in cov["by_category"].items()]
    return "\n".join(md) + "\n"


def donut(pct, color, label, sub):
    """Return SVG markup for a KPI donut gauge (no JS, no external lib)."""
    r, c = 52, 2 * math.pi * 52
    dash = c * max(0, min(100, pct)) / 100
    return f"""
    <svg viewBox="0 0 120 120" class="donut" role="img" aria-label="{label} {pct} percent">
      <circle cx="60" cy="60" r="52" fill="none" stroke="#e9eef6" stroke-width="13"/>
      <circle cx="60" cy="60" r="52" fill="none" stroke="{color}" stroke-width="13"
        stroke-linecap="round" stroke-dasharray="{dash:.1f} {c:.1f}"
        transform="rotate(-90 60 60)"/>
      <text x="60" y="58" text-anchor="middle" class="donut-v">{pct}%</text>
      <text x="60" y="76" text-anchor="middle" class="donut-k">{sub}</text>
    </svg>"""


# --------------------------------------------------------------------------- #
# HTML (single template, server-rendered, inline SVG icons - no emoji)
# --------------------------------------------------------------------------- #

ICONS = {
    "chip": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/></svg>',
    "shield": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>',
    "play": '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',
    "down": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v14M6 11l6 6 6-6M5 21h14"/></svg>',
    "warn": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>',
    "reset": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>',
}

PAGE = """
<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Validation Copilot</title>
<link rel=preconnect href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap" rel=stylesheet>
<style>
 :root{
   --primary:#1e40af;--secondary:#3b82f6;--accent:#b45309;
   --bg:#f4f7fb;--surface:#ffffff;--ink:#0f1f3d;--mut:#5b6b86;--line:#dbe4f0;
   --ok:#15803d;--warnc:#b45309;--bad:#b91c1c;--radius:14px;
   --shadow:0 1px 2px rgba(16,33,68,.06),0 8px 24px rgba(16,33,68,.06);
 }
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--ink);
   font:15px/1.55 "Fira Sans",system-ui,Segoe UI,sans-serif}
 .mono{font-family:"Fira Code",ui-monospace,monospace;font-variant-numeric:tabular-nums}
 a{color:var(--primary)}
 header{background:linear-gradient(115deg,#13286b,#1e40af 55%,#2f5bd0);color:#fff;
   padding:22px 30px;display:flex;align-items:center;gap:14px}
 header svg{width:30px;height:30px;color:#bcd0ff}
 header h1{margin:0;font:600 1.3rem/1.2 "Fira Code",monospace;letter-spacing:-.01em}
 header p{margin:3px 0 0;color:#c7d6ff;font-size:.9rem}
 .tabs{display:flex;gap:4px;padding:0 30px;background:#16306f}
 .tab{display:flex;align-items:center;gap:7px;padding:12px 18px;color:#a9bfee;
   text-decoration:none;font-weight:500;border-bottom:3px solid transparent;cursor:pointer}
 .tab svg{width:17px;height:17px}
 .tab:hover{color:#fff}.tab.active{color:#fff;border-bottom-color:var(--accent)}
 .wrap{display:flex;gap:22px;padding:24px 30px;align-items:flex-start;flex-wrap:wrap}
 .card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
   box-shadow:var(--shadow)}
 .side{width:296px;flex:0 0 296px;padding:20px}
 .main{flex:1;min-width:340px;display:flex;flex-direction:column;gap:20px}
 h2{font:600 .72rem/1 "Fira Code",monospace;text-transform:uppercase;letter-spacing:.08em;
   color:var(--mut);margin:0 0 12px}
 label{display:block;margin:12px 0 5px;font-size:.88rem;font-weight:500}
 .opt{display:flex;gap:9px;align-items:flex-start;padding:10px 12px;border:1px solid var(--line);
   border-radius:10px;margin:7px 0;cursor:pointer;transition:.15s}
 .opt:hover{border-color:var(--secondary);background:#f7faff}
 .opt input{margin-top:3px}.opt b{display:block;font-size:.9rem}.opt span{color:var(--mut);font-size:.78rem}
 select,input[type=file]{width:100%;padding:8px;border:1px solid var(--line);border-radius:9px;
   background:#fff;font:inherit;font-size:.85rem}
 input[type=range]{width:100%;accent-color:var(--primary)}
 .switch{display:flex;align-items:center;gap:10px;margin:8px 0;cursor:pointer;font-size:.9rem;font-weight:500}
 .switch input{width:0;height:0;opacity:0}
 .track{width:42px;height:24px;background:var(--line);border-radius:99px;position:relative;transition:.2s;flex:0 0 auto}
 .track:after{content:"";position:absolute;top:3px;left:3px;width:18px;height:18px;background:#fff;
   border-radius:50%;transition:.2s;box-shadow:0 1px 3px rgba(0,0,0,.3)}
 .switch input:checked + .track{background:var(--primary)}
 .switch input:checked + .track:after{transform:translateX(18px)}
 .btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;margin-top:18px;
   padding:12px;border:0;border-radius:10px;background:var(--primary);color:#fff;font:600 .98rem "Fira Sans";
   cursor:pointer;transition:.15s}
 .btn:hover{background:#1b3aa0}.btn svg{width:16px;height:16px}
 .btn:disabled{opacity:.5;cursor:not-allowed}
 .muted{color:var(--mut);font-size:.82rem}
 .pad{padding:22px}
 /* gate banner */
 .banner{display:flex;align-items:center;gap:22px;flex-wrap:wrap;padding:20px 24px;border-radius:var(--radius);
   border:1px solid var(--line)}
 .gate-badge{font:700 1.15rem "Fira Code",monospace;padding:9px 18px;border-radius:10px;letter-spacing:.02em}
 .headline{font-size:1.08rem;font-weight:600;margin:0}
 .conf{margin-left:auto;text-align:right}
 .conf .pill{font:700 .9rem "Fira Code",monospace;padding:5px 13px;border-radius:8px;color:#fff}
 /* kpi grid */
 .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px}
 .kpi{padding:16px;text-align:center}
 .donut{width:120px;height:120px}
 .donut-v{font:700 1.45rem "Fira Code",monospace;fill:var(--ink)}
 .donut-k{font:500 .58rem "Fira Code",monospace;fill:var(--mut);text-transform:uppercase;letter-spacing:.05em}
 .kpi .big{font:700 2.2rem "Fira Code",monospace;display:block;margin:14px 0 6px}
 .kpi .lab{color:var(--mut);font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
 /* bars */
 .bar-row{display:grid;grid-template-columns:90px 1fr 86px;align-items:center;gap:12px;margin:9px 0}
 .bar-row .nm{font-size:.85rem;font-weight:500}
 .bar{height:11px;background:#eef3fa;border-radius:99px;overflow:hidden}
 .bar>span{display:block;height:100%;border-radius:99px;transition:width .4s ease}
 .bar-row .pc{font-family:"Fira Code",monospace;font-size:.8rem;text-align:right;color:var(--mut)}
 ul.risks{margin:6px 0 0;padding-left:18px}ul.risks li{margin:5px 0}
 .flag{display:flex;gap:9px;align-items:flex-start;background:#fff7ed;border:1px solid #fed7aa;
   border-radius:10px;padding:9px 12px;margin:7px 0;font-size:.86rem;color:#7c2d12}
 .flag svg{width:16px;height:16px;color:var(--accent);flex:0 0 auto;margin-top:1px}
 /* table */
 table{width:100%;border-collapse:collapse;font-size:.87rem}
 thead th{position:sticky;top:0;background:#f0f5fc;color:var(--mut);text-align:left;
   font:600 .72rem "Fira Code",monospace;text-transform:uppercase;letter-spacing:.05em;padding:10px 12px;
   border-bottom:1px solid var(--line)}
 tbody td{padding:10px 12px;border-bottom:1px solid #eef2f8;vertical-align:top}
 tbody tr:nth-child(even){background:#fafcff}
 tbody tr:hover{background:#f0f6ff}
 .pri{font:700 .72rem "Fira Code",monospace;color:#fff;padding:2px 9px;border-radius:99px}
 details{border:1px solid var(--line);border-radius:10px;padding:0;overflow:hidden;background:#fff}
 summary{cursor:pointer;font-weight:600;padding:13px 18px;font-size:.9rem;display:flex;gap:8px;align-items:center}
 summary svg{width:16px;height:16px;color:var(--mut)}
 details[open] summary{border-bottom:1px solid var(--line)}
 details .inner{padding:6px 18px 16px}
 pre{white-space:pre-wrap;font:.8rem/1.5 "Fira Code",monospace;color:#334155;margin:0}
 .dl{display:inline-flex;align-items:center;gap:8px;padding:10px 18px;border-radius:10px;background:var(--accent);
   color:#fff;text-decoration:none;font-weight:600;font-size:.88rem;width:fit-content}
 .dl svg{width:16px;height:16px}
 .reset{display:inline-flex;align-items:center;gap:7px;padding:9px 16px;border-radius:10px;
   background:#fff;color:var(--mut);border:1px solid var(--line);text-decoration:none;font-weight:600;font-size:.86rem}
 .reset:hover{border-color:var(--bad);color:var(--bad)}.reset svg{width:15px;height:15px}
 .empty{text-align:center;color:var(--mut);padding:46px 20px}
 .empty svg{width:40px;height:40px;color:#c3d2e8;margin-bottom:10px}
 .scoreband{display:inline-flex;align-items:center;gap:10px;font:700 1.05rem "Fira Code",monospace;
   padding:11px 20px;border-radius:11px;color:#fff}
 details.subcheck{border:1px solid #eef2f8;border-radius:9px;margin:8px 0;background:#fafcff}
 details.subcheck>summary{padding:10px 14px;font-size:.9rem}
 details.subcheck[open]>summary{border-bottom:1px solid #eef2f8}
 .verdict{font:700 .72rem "Fira Code",monospace;width:58px;display:inline-block}
 .dim{font:600 .68rem "Fira Code",monospace;text-transform:uppercase;letter-spacing:.04em;
   color:var(--primary);background:#eaf1fe;border:1px solid #cfe0fc;padding:2px 9px;border-radius:99px}
 table.fw{table-layout:fixed}
 table.fw th:nth-child(1){width:14%}table.fw th:nth-child(2){width:22%}
 table.fw td{font-size:.85rem;line-height:1.5}
 .proof{padding:4px 16px 14px}
 details.tech{border:0;background:transparent;border-radius:0;margin:4px 0 0}
 details.tech>summary{padding:6px 0;font:600 .76rem "Fira Code",monospace;color:var(--mut);
   text-transform:uppercase;letter-spacing:.04em}
 details.tech[open]>summary{border-bottom:0}
 .kv{display:grid;grid-template-columns:120px 1fr;gap:12px;padding:6px 0;border-top:1px solid #f0f4fa}
 .kvk{color:var(--mut);font:600 .72rem "Fira Code",monospace;text-transform:uppercase;letter-spacing:.04em}
 .kvv{font-size:.86rem;word-break:break-word}
 @media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body>
<header>{{ icons.chip|safe }}
 <div><h1>Post-Silicon Validation Copilot</h1>
  <p>Deterministic coverage &middot; calibrated confidence &middot; LLM gate-review readout</p></div>
</header>
<nav class=tabs>
 <a class="tab {{ 'active' if tab!='trust' }}" href="/">{{ icons.chip|safe }} Gate Readout</a>
 <a class="tab {{ 'active' if tab=='trust' }}" href="/trust">{{ icons.shield|safe }} Trust &amp; Evaluation</a>
</nav>

{% if tab != 'trust' %}
<div class=wrap>
 <form class="card side" method=post action="/run" enctype=multipart/form-data>
  <h2>1 &middot; Data source</h2>
  <label class=opt><input type=radio name=source value=sample_jira {{ 'checked' if sel=='sample_jira' or sel not in ['sample_logs','sample_clean','upload'] }}>
   <span style=flex:1><b>Sample Jira board</b><span>Messy real-world tracking &rarr; NO-GO, flags hygiene gaps</span></span></label>
  <label class=opt><input type=radio name=source value=sample_clean {{ 'checked' if sel=='sample_clean' }}>
   <span style=flex:1><b>Sample clean release</b><span>Everything passes &amp; tracked &rarr; GO / HIGH</span></span></label>
  <label class=opt><input type=radio name=source value=sample_logs {{ 'checked' if sel=='sample_logs' }}>
   <span style=flex:1><b>Sample bench logs</b><span>Clean lab data, no Jira hygiene layer</span></span></label>
  <label class=opt><input type=radio name=source value=upload {{ 'checked' if sel=='upload' }}>
   <span style=flex:1><b>Upload my own files</b><span>Bring a plan + a status CSV</span></span></label>

  <label>Plan CSV (upload mode)</label><input type=file name=plan accept=.csv>
  <label>Status CSV (upload mode)</label><input type=file name=status accept=.csv>
  <select name=status_type><option value=jira>Status type: Jira export</option><option value=logs>Status type: Bench logs</option></select>

  <h2 style=margin-top:22px>2 &middot; Narrative engine</h2>
  <label class=switch><input type=checkbox name=use_llm {{ 'checked' if use_llm }}><span class=track></span>Use Claude for the narrative</label>
  <div class=muted>Off = transparent rule-based readout (no API key, no cost).</div>
  <label>Flag Jira tickets stale after <b class=mono id=sd>{{ stale_days }}</b> days</label>
  <input type=range name=stale_days min=3 max=60 value="{{ stale_days }}"
    oninput="document.getElementById('sd').textContent=this.value">

  <button class=btn type=submit>{{ icons.play|safe }} Run analysis</button>
 </form>

 <div class=main>
 {% if not result %}
  <div class="card empty">{{ icons.chip|safe }}
   <div style="font-weight:600;font-size:1.05rem;margin-bottom:4px">No analysis yet</div>
   <div class=muted>Pick a data source on the left and click <b>Run analysis</b>.<br>
    The gate call, KPIs, coverage charts, risks and actions appear here.</div>
  </div>
 {% else %}
  {% set r = result %}
  <div class=banner style="background:{{ gate_bg }}">
   <span class=gate-badge style="background:{{ gate_color }};color:#fff">{{ gate_label }}</span>
   <p class=headline>{{ r.headline }}</p>
   <div class=conf><div class=muted>Confidence</div>
    <span class=pill style="background:{{ conf_color }}">{{ r.confidence|upper }}</span></div>
  </div>
  <div class=muted style=margin-top:-8px>Source: {{ src }} &nbsp;&middot;&nbsp; Engine: {{ engine }}
    &nbsp;&middot;&nbsp; Basis: {{ r.confidence_rationale }}</div>

  <div class="card pad">
   <h2>Key indicators</h2>
   <div class=kpis>
    <div class=kpi>{{ donut_cov|safe }}<div class=lab>Coverage</div></div>
    <div class=kpi>{{ donut_pass|safe }}<div class=lab>Pass rate</div></div>
    <div class=kpi><span class=big style="color:{{ '#b91c1c' if cov.critical_gaps|length else '#15803d' }}">{{ cov.critical_gaps|length }}</span><div class=lab>Critical not passing</div></div>
    <div class=kpi><span class=big style=color:var(--primary)>{{ cov.total_planned }}</span><div class=lab>Planned tests</div></div>
   </div>
  </div>

  <div class=wrap style="padding:0;gap:20px">
   <div class="card pad" style="flex:1;min-width:300px">
    <h2>Coverage by category</h2>
    {% for cat, v in cov.by_category.items() %}
     {% set bc = '#15803d' if v.pct==100 else ('#b45309' if v.pct>=50 else '#b91c1c') %}
     <div class=bar-row><span class=nm>{{ cat }}</span>
      <span class=bar><span style="width:{{ v.pct }}%;background:{{ bc }}"></span></span>
      <span class=pc>{{ v.passed }}/{{ v.planned }} &middot; {{ v.pct }}%</span></div>
    {% endfor %}
   </div>
   <div class="card pad" style="flex:1;min-width:300px">
    <h2>Top risks</h2>
    {% if r.top_risks %}<ul class=risks>{% for x in r.top_risks %}<li>{{ x }}</li>{% endfor %}</ul>
    {% else %}<div class=muted>No outstanding release risks.</div>{% endif %}
    {% if r.tracking_hygiene %}
     <h2 style=margin-top:18px>Tracking hygiene &mdash; Jira not 100%</h2>
     {% for h in r.tracking_hygiene %}<div class=flag>{{ icons.warn|safe }}<span>{{ h }}</span></div>{% endfor %}
    {% endif %}
   </div>
  </div>

  <div class="card pad">
   <h2>Prioritized actions</h2>
   {% if actions %}
   <table><thead><tr><th>Priority</th><th>Owner area</th><th>Action</th></tr></thead><tbody>
    {% for a in actions %}<tr>
      <td><span class=pri style="background:{{ a.color }}">{{ a.priority }}</span></td>
      <td>{{ a.owner_area }}</td><td>{{ a.action }}</td></tr>{% endfor %}
   </tbody></table>
   {% else %}<div class=muted>No actions required &mdash; clean to proceed.</div>{% endif %}
  </div>

  {% if dq %}
  <details class=card><summary>{{ icons.shield|safe }} Data-quality detail &mdash; where Jira can't be trusted</summary>
   <div class=inner>
   {% for title, items, fields in dq %}
    <h2 style=margin-top:14px>{{ title }}</h2>
    {% if items %}<table><thead><tr>{% for f in fields %}<th>{{ f }}</th>{% endfor %}</tr></thead><tbody>
     {% for i in items %}<tr>{% for f in fields %}<td class="{{ 'mono' if f in ['test_id','jira_key'] }}">{{ i.get(f,'') }}</td>{% endfor %}</tr>{% endfor %}</tbody></table>
    {% else %}<div class=muted>(none)</div>{% endif %}
   {% endfor %}
   </div>
  </details>
  {% endif %}

  <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
   <a class=dl download="readout.md" href="data:text/markdown;base64,{{ md_b64 }}">{{ icons.down|safe }} Download readout.md</a>
   <a class=reset href="/reset">{{ icons.reset|safe }} Reset</a>
   <span class=muted>This result stays here while you switch tabs &mdash; Reset (or closing the app) clears it.</span>
  </div>
  <details class=card><summary>{{ icons.chip|safe }} Deterministic fact brief (exactly what the LLM is given)</summary>
   <div class=inner><pre>{{ facts }}</pre></div></details>
 {% endif %}
 </div>
</div>

{% else %}
<div class=wrap><div class="main">
 <div class="card pad">
  <h2>How do we know it's trustworthy?</h2>
  <p>The AI never computes a number &mdash; it only turns a pre-verified, grounded fact brief
     into judgment. An evaluation harness then scores every readout against labeled scenarios
     on the six dimensions below and <b>fails closed</b> if any check fails.</p>
  <form method=post action="/eval" style="display:flex;gap:18px;align-items:center;flex-wrap:wrap">
   <label class=switch><input type=checkbox name=use_llm {{ 'checked' if use_llm }}><span class=track></span>Score the live Claude output</label>
   <button class=btn type=submit style="width:auto;margin:0;padding:11px 20px">{{ icons.play|safe }} Run evaluation harness</button>
   {% if scenarios %}<a class=reset href="/reset-eval">{{ icons.reset|safe }} Reset</a>{% endif %}
  </form>
 </div>

 <div class="card pad">
  <h2>The trust framework &mdash; what each check means and why it's there</h2>
  <p class=muted style=margin-bottom:14px>This is the map. Every result the harness produces below
   traces back to one of these six dimensions. Read this once and the rest is self-explanatory.</p>
  <table class=fw><thead><tr>
    <th>Dimension</th><th>What it guarantees</th><th>Why it's there</th><th>How it's proven</th>
  </tr></thead><tbody>
   {% for d in framework %}<tr>
     <td><span class=dim>{{ d.term }}</span></td>
     <td style=font-weight:600>{{ d.guarantee }}</td>
     <td class=muted>{{ d.why }}</td>
     <td class=muted>{{ d.how }}</td>
   </tr>{% endfor %}
  </tbody></table>
 </div>
 {% if scenarios %}
  <div class="card pad">
   <span class=scoreband style="background:{{ score_color }}">{{ icons.shield|safe }} SCORECARD &mdash; {{ passed }}/{{ total }} checks passed ({{ pct }}%)</span>
   <p class=muted style=margin-bottom:0>Each scenario is a labeled plan + Jira export with a known-correct answer.
    Expand any check to see the expected value, what the tool produced, and why it passed.</p>
  </div>
  {% for s in scenarios %}
  <details class=card {{ 'open' if loop.first }}>
   <summary>{{ icons.shield|safe }}
    <span class="pri" style="background:{{ '#15803d' if s.passed==s.total else '#b91c1c' }};margin-right:10px">{{ s.passed }}/{{ s.total }}</span>
    <span style=font-weight:600>{{ s.plain_scenario.split(':')[0] }}</span>
    <span class=muted style="font-weight:400;margin-left:8px">&rarr; verdict: {{ s.gate_word }}</span>
   </summary>
   <div class=inner>
    <p style=margin-top:12px>{{ s.plain_scenario }}</p>
    <p class=muted style=margin-bottom:14px>The tool got this case <b>{{ s.passed }} out of {{ s.total }}</b> right.
     Expand any line below to read, in plain language, what it checked and why it can be trusted.</p>
    {% for c in s.checks %}
    {% set fw = framework_map[c.key] %}
    <details class=subcheck>
     <summary>
      <span class=verdict style="color:{{ '#15803d' if c.ok else '#b91c1c' }}">{{ '✓ PASS' if c.ok else '✗ FAIL' }}</span>
      <span style=font-weight:600>{{ c.title }}</span>
      <span class=dim style=margin-left:8px>{{ fw.term }}</span>
     </summary>
     <div class=proof>
      <p style="margin:10px 0;line-height:1.6">{{ c.plain }}</p>
      <div class=kv><span class=kvk>Why it's there</span><span class=kvv>{{ fw.why }}</span></div>
      <div class=kv><span class=kvk>How it's proven</span><span class=kvv>{{ fw.how }}</span></div>
      <details class=tech><summary>Technical detail (the raw receipt)</summary>
       <div style=padding:6px_2px>
        <div class=kv><span class=kvk>Expected</span><span class="kvv mono">{{ c.expected }}</span></div>
        <div class=kv><span class=kvk>Actual</span><span class="kvv mono">{{ c.actual }}</span></div>
       </div>
      </details>
     </div>
    </details>
    {% endfor %}
   </div>
  </details>
  {% endfor %}
 {% endif %}
 <div class="card pad">
  <h2>The six dimensions</h2>
  <p class=muted>determinism &middot; decision accuracy &middot; confidence calibration &middot;
   grounding (no fabricated IDs) &middot; critical recall &middot; hygiene recall.
   See <span class=mono>EVALUATION.md</span> for the full framework and trust flowchart.</p>
 </div>
</div></div>
{% endif %}
</body></html>
"""


@app.route("/")
def index():
    if LAST_READOUT is not None:
        return render_template_string(PAGE, **LAST_READOUT)
    return render_template_string(PAGE, tab="readout", result=None, icons=ICONS,
                                  sel="sample_jira", use_llm=False, stale_days=14)


@app.route("/reset")
def reset():
    global LAST_READOUT
    LAST_READOUT = None
    return redirect("/")


@app.route("/run", methods=["POST"])
def run():
    f = request.form
    source = f.get("source", "sample_jira")
    use_llm = bool(f.get("use_llm"))
    stale_days = int(f.get("stale_days", 14))
    plan_path = jira_path = logs_path = None

    if source == "sample_jira":
        plan_path, jira_path = os.path.join(SAMPLE, "validation_plan.csv"), os.path.join(SAMPLE, "jira_export.csv")
    elif source == "sample_clean":
        plan_path, jira_path = os.path.join(SAMPLE, "clean_release_plan.csv"), os.path.join(SAMPLE, "clean_release_jira.csv")
    elif source == "sample_logs":
        plan_path, logs_path = os.path.join(SAMPLE, "validation_plan.csv"), os.path.join(SAMPLE, "bench_logs.csv")
    else:
        up_plan, up_status = request.files.get("plan"), request.files.get("status")
        plan_path = _save_upload(up_plan) if up_plan and up_plan.filename else os.path.join(SAMPLE, "validation_plan.csv")
        if up_status and up_status.filename:
            if f.get("status_type") == "logs":
                logs_path = _save_upload(up_status)
            else:
                jira_path = _save_upload(up_status)
        else:
            jira_path = os.path.join(SAMPLE, "jira_export.csv")

    cov, src = build_coverage(plan_path, jira_path, logs_path, stale_days)
    engine = "Claude (claude-opus-4-8)" if use_llm else "rule-based (offline)"
    try:
        r = (copilot.generate_readout(copilot.render_facts(cov)) if use_llm
             else copilot.rule_based_readout(cov))
    except SystemExit:
        r = copilot.rule_based_readout(cov)
        engine = "rule-based (offline) - no API key found"

    actions = [{"priority": a.priority, "owner_area": a.owner_area, "action": a.action,
                "color": P_COLOR.get(a.priority, "#475569")}
               for a in sorted(r.actions, key=lambda x: x.priority)]
    dq = None
    if "untracked_in_jira" in cov:
        dq = [
            ("Untracked - required but not on the board", cov["untracked_in_jira"],
             ["test_id", "category", "priority", "description"]),
            ("Ambiguous - closed with no recorded result", cov["ambiguous"],
             ["test_id", "category", "priority", "notes"]),
            ("Stale - in-flight, not updated recently", cov.get("stale_tickets", []),
             ["test_id", "category", "priority", "notes"]),
            ("Orphan - in Jira, not in the plan", cov["orphan_tickets"],
             ["jira_key", "test_id", "summary"]),
        ]
    md_b64 = base64.b64encode(readout_markdown(cov, r).encode("utf-8")).decode("ascii")
    gate_color, gate_bg, gate_label = GATE[r.gate_recommendation]

    global LAST_READOUT
    LAST_READOUT = dict(
        tab="readout", result=r, cov=cov, src=src, engine=engine, icons=ICONS,
        gate_color=gate_color, gate_bg=gate_bg, gate_label=gate_label, conf_color=CONF[r.confidence],
        donut_cov=donut(cov["coverage_pct"], CONF[r.confidence], "Coverage", "covered"),
        donut_pass=donut(cov["pass_rate_pct"], "#1e40af", "Pass rate", "passing"),
        actions=actions, dq=dq, md_b64=md_b64, facts=copilot.render_facts(cov),
        sel=source, use_llm=use_llm, stale_days=stale_days)
    return render_template_string(PAGE, **LAST_READOUT)


@app.route("/trust")
def trust():
    if LAST_EVAL is not None:
        return render_template_string(PAGE, **LAST_EVAL)
    return render_template_string(PAGE, tab="trust", scenarios=None, use_llm=False, icons=ICONS,
                                  framework=FRAMEWORK, framework_map=FRAMEWORK_MAP)


@app.route("/reset-eval")
def reset_eval():
    global LAST_EVAL
    LAST_EVAL = None
    return redirect("/trust")


@app.route("/eval", methods=["POST"])
def run_eval():
    import eval_harness
    use_llm = bool(request.form.get("use_llm"))
    scenarios, total, passed = [], 0, 0
    dirs = sorted(os.path.join(eval_harness.SCENARIOS_DIR, x)
                  for x in os.listdir(eval_harness.SCENARIOS_DIR)
                  if os.path.isdir(os.path.join(eval_harness.SCENARIOS_DIR, x)))
    for d in dirs:
        s = eval_harness.evaluate_detailed(d, use_llm)
        scenarios.append(s)
        total += s["total"]
        passed += s["passed"]
    pct = round(100 * passed / total, 1) if total else 0.0
    score_color = "#15803d" if passed == total else "#b91c1c"
    global LAST_EVAL
    LAST_EVAL = dict(tab="trust", scenarios=scenarios, total=total, icons=ICONS,
                     passed=passed, pct=pct, score_color=score_color, use_llm=use_llm,
                     framework=FRAMEWORK, framework_map=FRAMEWORK_MAP)
    return render_template_string(PAGE, **LAST_EVAL)


if __name__ == "__main__":
    print("\n  Post-Silicon Validation Copilot UI")
    print("  Open  http://127.0.0.1:5000  in your browser  (Ctrl+C to stop)\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
