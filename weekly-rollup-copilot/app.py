"""Weekly Program Roll-Up Copilot - clickable web UI (Flask).

A thin Flask front end over the SAME deterministic core and writer the CLI uses
(status_adapter.py / intelligence.py / rollup.py). No analysis logic lives here - the
UI collects inputs, calls the trusted functions, and renders the result. Every RAG
color, age, and count on screen is the same auditable value the eval harness scores;
nothing is recomputed for display.

Flask is used (not Streamlit) because it is pure-Python and installs everywhere
including Windows. Charts/icons are inline SVG (no external library, works offline).

Run it:
    python app.py
then open http://127.0.0.1:5000
"""

import math
import os
import sys
import tempfile

from flask import Flask, render_template_string, request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "evals"))

import rollup            # noqa: E402
import status_adapter    # noqa: E402

SAMPLE = os.path.join(HERE, "sample_data")
app = Flask(__name__)

# Last-run state, kept in memory so switching tabs doesn't lose your result.
LAST = None   # dict: update, facts, markdown, source, used_llm

RAG = {
    "green": ("#15803d", "#dcfce7", "GREEN"),
    "amber": ("#b45309", "#fef3c7", "AMBER"),
    "red":   ("#b91c1c", "#fee2e2", "RED"),
}
CONF = {"high": "#15803d", "medium": "#b45309", "low": "#b91c1c"}
STATE_COLOR = {
    "blocked": "#b91c1c", "slipped": "#b45309", "new": "#1d4ed8",
    "in_progress": "#475569", "done": "#15803d",
}
P_COLOR = {"P0": "#b91c1c", "P1": "#b45309", "P2": "#475569"}

# The trust framework: one row per eval check, decoding what it guarantees and how.
FRAMEWORK = [
    {"term": "Determinism", "guarantee": "The status colors are repeatable & correct",
     "why": "A weekly call must be defensible. Identical inputs must always yield the same RAG, "
            "so anyone can reproduce how the call was reached.",
     "how": "All RAG, action aging, and rollup math runs in plain Python — never the AI — and is "
            "checked against a hand-labeled answer for each scenario."},
    {"term": "Decision accuracy", "guarantee": "It calls overall program health right",
     "why": "The headline green/amber/red is what leadership reacts to. If it's wrong, the whole "
            "update misleads.",
     "how": "The overall RAG is compared to the call an experienced PM already agreed is correct."},
    {"term": "Confidence calibration", "guarantee": "Its confidence is honest, not inflated",
     "why": "The dangerous failure is a confident update built on stale or missing data. Confidence "
            "must reflect how complete the week's inputs actually are.",
     "how": "Confidence is computed from data completeness (stale updates, uncovered workstreams, "
            "unowned actions) and the AI is forced to adopt it."},
    {"term": "Grounding", "guarantee": "It never invents an action, decision, or quote",
     "why": "An AI that fabricates a decision or an owner would launder a false picture into an "
            "authoritative update. Every item must trace to a real sentence.",
     "how": "Every extracted action and decision carries the exact source quote, and the harness "
            "checks that quote actually appears in the meeting notes."},
    {"term": "Action recall", "guarantee": "It catches the action items raised in meetings",
     "why": "The point is to not drop the ball. A silently missed action item is exactly the failure "
            "the weekly roll-up exists to prevent.",
     "how": "Every action item seeded in the meeting notes must appear in the open-actions list."},
    {"term": "Hot-topic recall", "guarantee": "It surfaces what's actually hot",
     "why": "Themes raised across several meetings are what leadership should hear first; a one-off "
            "comment shouldn't outrank them.",
     "how": "Topics are ranked by how many separate meetings raised them; the recurring theme must "
            "rank first."},
]


# --------------------------------------------------------------------------- #
# Core wiring - reuse the exact CLI functions
# --------------------------------------------------------------------------- #

def _save(file_storage, suffix):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="wb")
    tmp.write(file_storage.read())
    tmp.close()
    return tmp.name


def run_rollup(form, files):
    use_llm = form.get("use_llm") == "on"
    use_sample = form.get("source", "sample") == "sample"

    # Graceful fallback: if Claude was requested but no API key is configured, run the
    # deterministic offline draft instead of crashing, and tell the user why.
    notice = ""
    if use_llm and not os.environ.get("ANTHROPIC_API_KEY"):
        use_llm = False
        notice = ("No ANTHROPIC_API_KEY found, so this is the deterministic schedule + action-log "
                  "draft (meetings were not mined). Add your key to a .env file and re-run to enable "
                  "meeting mining and the AI-written narrative.")

    if use_sample:
        ws = os.path.join(SAMPLE, "workstreams.csv")
        ms = os.path.join(SAMPLE, "milestones.csv")
        al = os.path.join(SAMPLE, "action_log.csv")
        meetings_dir = os.path.join(SAMPLE, "meetings")
        source = "sample data"
    else:
        ws = _save(files["workstreams"], ".csv") if files.get("workstreams") else os.path.join(SAMPLE, "workstreams.csv")
        ms = _save(files["milestones"], ".csv") if files.get("milestones") else os.path.join(SAMPLE, "milestones.csv")
        al = _save(files["actions"], ".csv") if files.get("actions") else os.path.join(SAMPLE, "action_log.csv")
        uploaded = [f for f in files.getlist("meetings") if f and f.filename]
        if uploaded:
            meetings_dir = tempfile.mkdtemp()
            for f in uploaded:
                with open(os.path.join(meetings_dir, os.path.basename(f.filename)), "wb") as out:
                    out.write(f.read())
        else:
            meetings_dir = os.path.join(SAMPLE, "meetings")
        source = "uploaded files"

    facts, update = rollup.build(ws, ms, al, meetings_dir=meetings_dir, use_llm=use_llm)
    return {
        "update": update, "facts": facts, "markdown": rollup.to_markdown(update),
        "source": source, "used_llm": use_llm, "notice": notice,
    }


# --------------------------------------------------------------------------- #
# Small render helpers (inline SVG, no JS libs)
# --------------------------------------------------------------------------- #

def donut(pct, color, sub):
    c = 2 * math.pi * 52
    dash = c * max(0, min(100, pct)) / 100
    return f"""<svg viewBox="0 0 120 120" class="donut">
      <circle cx="60" cy="60" r="52" fill="none" stroke="#e9eef6" stroke-width="13"/>
      <circle cx="60" cy="60" r="52" fill="none" stroke="{color}" stroke-width="13"
        stroke-linecap="round" stroke-dasharray="{dash:.1f} {c:.1f}" transform="rotate(-90 60 60)"/>
      <text x="60" y="58" text-anchor="middle" class="donut-v">{pct}%</text>
      <text x="60" y="76" text-anchor="middle" class="donut-k">{sub}</text></svg>"""


def chip(text, color, bg):
    return f'<span class="chip" style="color:{color};background:{bg}">{text}</span>'


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_update(u):
    fg, bg, label = RAG[u.overall_rag]
    greens = sum(1 for w in u.workstreams if w.rag == "green")
    pct_green = round(100 * greens / len(u.workstreams)) if u.workstreams else 0
    n_actions = len(u.open_actions)
    n_blocked = sum(1 for a in u.open_actions if a.state in ("blocked", "slipped"))

    # Banner + KPIs
    html = [f"""<div class="banner" style="background:{bg};border-color:{fg}">
      <div><div class="banner-rag" style="color:{fg}">{label}</div>
        <div class="banner-head">{esc(u.headline)}</div></div>
      <div class="banner-conf">{chip('confidence: ' + u.confidence, '#fff', CONF[u.confidence])}</div>
    </div>
    <div class="kpis">
      <div class="kpi">{donut(pct_green, fg, 'green')}<div class="kpi-l">workstreams green</div></div>
      <div class="kpi"><div class="kpi-n">{n_actions}</div><div class="kpi-l">open actions</div></div>
      <div class="kpi"><div class="kpi-n" style="color:{P_COLOR['P0']}">{n_blocked}</div><div class="kpi-l">blocked / slipped</div></div>
      <div class="kpi"><div class="kpi-n">{len(u.hot_topics)}</div><div class="kpi-l">hot topics</div></div>
      <div class="kpi"><div class="kpi-n" style="color:{P_COLOR['P1']}">{len(u.schedule_slips)}</div><div class="kpi-l">schedule slips</div></div>
    </div>"""]

    # Executive summary
    html.append(f'<div class="card"><h3>Executive summary</h3><p class="lead">{esc(u.executive_summary)}</p></div>')

    # Workstream cards
    cards = []
    for w in u.workstreams:
        wf, wb, wl = RAG[w.rag]
        working = "".join(f'<li class="ok">{esc(x)}</li>' for x in w.whats_working) or '<li class="muted">—</li>'
        notw = "".join(f'<li class="bad">{esc(x)}</li>' for x in w.whats_not) or '<li class="muted">—</li>'
        cards.append(f"""<div class="ws" style="border-top:4px solid {wf}">
          <div class="ws-h"><span class="ws-name">{esc(w.workstream)}</span>{chip(wl, wf, wb)}</div>
          <p class="ws-sum">{esc(w.summary)}</p>
          <div class="ws-cols">
            <div><div class="col-t ok-t">What's working</div><ul>{working}</ul></div>
            <div><div class="col-t bad-t">What's not</div><ul>{notw}</ul></div>
          </div></div>""")
    html.append(f'<h3 class="sec">Workstreams</h3><div class="ws-grid">{"".join(cards)}</div>')

    # Hot topics
    if u.hot_topics:
        rows = "".join(
            f'<div class="hot"><span class="hot-badge">{h.mentions}×</span>'
            f'<div><b>{esc(h.topic)}</b><div class="hot-why">{esc(h.why_it_matters)}</div></div></div>'
            for h in u.hot_topics)
        html.append(f'<div class="card"><h3>Hot topics <span class="hint">recurring across meetings</span></h3>{rows}</div>')

    # Decisions
    if u.decisions:
        rows = "".join(
            f'<li><b>{esc(d.decision)}</b>{(" — " + esc(d.rationale)) if d.rationale else ""}'
            f'<div class="src">{esc(getattr(d, "_meeting", ""))}: "{esc(d.source_quote[:120])}"</div></li>'
            for d in u.decisions)
        html.append(f'<div class="card"><h3>Decisions made</h3><ul class="dec">{rows}</ul></div>')

    # Open actions table
    if u.open_actions:
        rows = ""
        for a in u.open_actions:
            sc = STATE_COLOR.get(a.state, "#475569")
            age = f"{a.age_days}d" if a.age_days is not None else "new"
            rows += (f'<tr><td>{chip(a.state, "#fff", sc)}</td><td>{esc(a.action)}'
                     f'<div class="src">{esc(a.source[:90])}</div></td>'
                     f'<td>{esc(a.owner)}</td><td class="mono">{esc(a.due or "-")}</td>'
                     f'<td class="mono">{age}</td></tr>')
        html.append(f'<div class="card"><h3>Open action items</h3><table class="tbl">'
                    f'<tr><th>State</th><th>Action</th><th>Owner</th><th>Due</th><th>Age</th></tr>{rows}</table></div>')

    # Schedule slips + Asks
    if u.schedule_slips:
        items = "".join(f'<li class="bad">{esc(s)}</li>' for s in u.schedule_slips)
        html.append(f'<div class="card"><h3>Schedule slips</h3><ul class="flat">{items}</ul></div>')
    if u.asks:
        rows = "".join(f'<tr><td>{chip(a.priority, "#fff", P_COLOR[a.priority])}</td>'
                       f'<td>{esc(a.owner_area)}</td><td>{esc(a.ask)}</td></tr>'
                       for a in sorted(u.asks, key=lambda x: x.priority))
        html.append(f'<div class="card"><h3>Asks for leadership</h3><table class="tbl">'
                    f'<tr><th>Pri</th><th>To</th><th>Ask</th></tr>{rows}</table></div>')

    # Data hygiene
    if u.data_hygiene:
        items = "".join(f'<li>{esc(h)}</li>' for h in u.data_hygiene)
        html.append(f'<div class="card warn"><h3>Data hygiene / gaps</h3><ul class="flat warn-l">{items}</ul>'
                    f'<p class="hint">These lower confidence — the status of these items is unverified.</p></div>')

    # "Your take" panel + export
    takes = "".join(
        f'<div class="take-row"><label>{esc(p)}</label>'
        f'<textarea class="take" data-prompt="{esc(p)}" rows="2" '
        f'placeholder="Your judgment here..."></textarea></div>'
        for p in u.pm_take_prompts)
    html.append(f"""<div class="card take-card"><h3>Your take <span class="hint">add before sending</span></h3>
      {takes}
      <div class="exp">
        <button class="btn" onclick="copyMd()">Copy as Markdown</button>
        <button class="btn ghost" onclick="downloadMd()">Download .md</button>
        <span id="copied" class="copied"></span>
      </div></div>""")
    return "\n".join(html)


# --------------------------------------------------------------------------- #
# Page template
# --------------------------------------------------------------------------- #

PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Weekly Roll-Up Copilot</title>
<link rel=preconnect href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap" rel=stylesheet>
<style>
:root{--primary:#1e40af;--secondary:#3b82f6;--accent:#b45309;
  --bg:#f4f7fb;--surface:#fff;--ink:#0f1f3d;--mut:#5b6b86;--line:#dbe4f0;--radius:14px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"Fira Sans",system-ui,sans-serif;line-height:1.5}
.mono{font-family:"Fira Code",ui-monospace,monospace;font-variant-numeric:tabular-nums}
header{background:linear-gradient(120deg,#0f1f3d,#1e40af);color:#fff;padding:22px 30px}
header h1{margin:0;font-size:1.35rem;font-weight:700;display:flex;align-items:center;gap:11px}
header p{margin:4px 0 0;color:#c7d6f0;font-size:.9rem}
.wrap{max-width:1120px;margin:0 auto;padding:22px}
.tabs{display:flex;gap:4px;border-bottom:2px solid var(--line);margin-bottom:20px}
.tab{padding:10px 18px;border:none;background:none;color:var(--mut);font-weight:600;font-size:.92rem;
  cursor:pointer;border-bottom:3px solid transparent;margin-bottom:-2px;font-family:inherit}
.tab:hover{color:var(--ink)}.tab.active{color:var(--primary);border-bottom-color:var(--accent)}
.panel{display:none}.panel.active{display:block}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 2px rgba(15,31,61,.04)}
.card h3{margin:0 0 12px;font-size:1rem}
.sec{margin:22px 0 12px;font-size:1.05rem}
.hint{color:var(--mut);font-weight:400;font-size:.8rem}
.lead{font-size:1.02rem;margin:0}
.chip{display:inline-block;padding:2px 10px;border-radius:99px;font-size:.74rem;font-weight:700;
  letter-spacing:.03em;text-transform:uppercase}
.banner{display:flex;justify-content:space-between;align-items:center;border:2px solid;
  border-radius:var(--radius);padding:18px 22px;margin-bottom:18px}
.banner-rag{font-size:1.6rem;font-weight:800;letter-spacing:.04em}
.banner-head{font-size:1.05rem;font-weight:500;margin-top:2px;max-width:760px}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:8px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px;text-align:center}
.kpi-n{font:700 2rem "Fira Code",monospace}
.kpi-l{color:var(--mut);font-size:.78rem;margin-top:2px}
.donut{width:84px;height:84px}.donut-v{font:700 1.3rem "Fira Code",monospace;fill:var(--ink)}
.donut-k{font:500 .58rem "Fira Sans";fill:var(--mut);text-transform:uppercase;letter-spacing:.05em}
.ws-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
.ws{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.ws-h{display:flex;justify-content:space-between;align-items:center}
.ws-name{font-weight:700}.ws-sum{color:var(--mut);font-size:.9rem;margin:6px 0 10px}
.ws-cols{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.col-t{font-size:.74rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px}
.ok-t{color:#15803d}.bad-t{color:#b91c1c}
.ws ul{margin:0;padding-left:16px}.ws li{font-size:.86rem;margin:3px 0}
li.ok::marker{content:"✓ ";color:#15803d}li.bad::marker{content:"! ";color:#b91c1c}
.muted{color:var(--mut);list-style:none;margin-left:-12px}
.hot{display:flex;gap:12px;align-items:flex-start;padding:9px 0;border-bottom:1px solid var(--line)}
.hot:last-child{border:none}
.hot-badge{background:var(--accent);color:#fff;font:700 .8rem "Fira Code";padding:3px 9px;border-radius:8px;flex:0 0 auto}
.hot-why{color:var(--mut);font-size:.85rem}
ul.dec{margin:0;padding-left:18px}ul.dec li{margin:8px 0}
.src{color:var(--mut);font-size:.74rem;font-style:italic;margin-top:2px}
.tbl{width:100%;border-collapse:collapse;font-size:.88rem}
.tbl th{text-align:left;color:var(--mut);font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;
  border-bottom:2px solid var(--line);padding:6px 8px}
.tbl td{padding:8px;border-bottom:1px solid var(--line);vertical-align:top}
ul.flat{margin:0;padding-left:18px}ul.flat li{margin:4px 0;font-size:.9rem}
.warn{background:#fffbeb;border-color:#fde68a}.warn-l li{color:#92400e}
.btn{background:var(--accent);color:#fff;border:none;padding:9px 16px;border-radius:9px;font-weight:600;
  font-size:.86rem;cursor:pointer;font-family:inherit}
.btn.ghost{background:#fff;color:var(--mut);border:1px solid var(--line)}
.take-card{background:#f8fafc}
.take-row{margin-bottom:10px}.take-row label{display:block;font-size:.85rem;font-weight:600;margin-bottom:3px}
textarea.take{width:100%;border:1px solid var(--line);border-radius:8px;padding:8px;font-family:inherit;font-size:.9rem}
.exp{display:flex;gap:10px;align-items:center;margin-top:8px}
.copied{color:#15803d;font-size:.84rem;font-weight:600}
.go{background:var(--primary);color:#fff;border:none;padding:12px 26px;border-radius:10px;font-weight:700;
  font-size:.95rem;cursor:pointer;font-family:inherit;display:inline-flex;gap:9px;align-items:center;
  margin-top:14px;transition:background .15s,box-shadow .15s,transform .05s}
.go:hover{background:#1b3aa3;box-shadow:0 4px 14px rgba(30,64,175,.28)}
.go:active{transform:translateY(1px)}
.go svg{width:18px;height:18px}
.fr{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px;background:#fff}
.fr h4{margin:0 0 4px;color:var(--primary)}.fr .g{color:var(--ink);font-weight:600;font-size:.9rem}
.fr .wy{color:var(--mut);font-size:.85rem;margin:4px 0}.fr .hw{font-size:.85rem}
/* Segmented control */
.seg{display:inline-flex;background:var(--muted,#eef2fb);border:1px solid var(--line);border-radius:11px;padding:4px;gap:4px}
.seg input{position:absolute;opacity:0;pointer-events:none}
.seg-btn{display:inline-flex;align-items:center;gap:8px;padding:9px 18px;border-radius:8px;font-weight:600;
  font-size:.9rem;color:var(--mut);cursor:pointer;transition:background .15s,color .15s,box-shadow .15s}
.seg-btn svg{width:17px;height:17px}
.seg input:checked+.seg-btn{background:#fff;color:var(--primary);box-shadow:0 1px 3px rgba(15,31,61,.12)}
.seg input:focus-visible+.seg-btn{outline:2px solid var(--primary);outline-offset:2px}
.note{margin-top:14px;background:#eef4ff;border:1px solid #c7d6f0;border-radius:10px;padding:12px 14px;
  font-size:.88rem;color:#27406e}
/* Upload reveal */
.uprows{display:none;margin-top:14px}.uprows.show{display:block}
.up-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.drop{position:relative;display:flex;flex-direction:column;gap:3px;border:1.5px dashed var(--line);border-radius:11px;
  padding:14px 16px;cursor:pointer;background:#fbfdff;transition:border-color .15s,background .15s}
.drop:hover{border-color:var(--secondary);background:#f3f8ff}
.drop:focus-within{border-color:var(--primary);box-shadow:0 0 0 3px rgba(37,99,235,.15)}
.drop-t{font-weight:600;font-size:.88rem}
.drop-h{font-size:.8rem;color:var(--mut);font-family:"Fira Code",monospace}
.drop.filled{border-style:solid;border-color:#15803d;background:#f0fdf4}
.drop.filled .drop-h{color:#15803d}
.drop input{position:absolute;inset:0;opacity:0;cursor:pointer}
.tpl{margin-top:12px;font-size:.85rem;color:var(--mut)}
.tpl a{color:var(--primary);font-weight:600;text-decoration:none}.tpl a:hover{text-decoration:underline}
.tpl-h{margin-top:4px;font-size:.8rem;color:var(--mut)}
/* Toggle switch */
.switch-row{display:flex;gap:12px;align-items:flex-start;cursor:pointer}
.sw{position:relative;flex:0 0 auto;margin-top:2px}
.sw input{position:absolute;opacity:0;width:44px;height:24px;margin:0;cursor:pointer}
.sw-track{display:block;width:44px;height:24px;background:#c3cee0;border-radius:99px;transition:background .18s}
.sw-track::after{content:"";position:absolute;top:3px;left:3px;width:18px;height:18px;background:#fff;border-radius:50%;
  box-shadow:0 1px 2px rgba(0,0,0,.25);transition:transform .18s}
.sw input:checked+.sw-track{background:var(--accent)}
.sw input:checked+.sw-track::after{transform:translateX(20px)}
.sw input:focus-visible+.sw-track{outline:2px solid var(--primary);outline-offset:2px}
a:focus-visible,button:focus-visible,.tab:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
@media (max-width:720px){.kpis{grid-template-columns:repeat(2,1fr)}.ws-grid,.up-grid{grid-template-columns:1fr}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body>
<header>
  <h1><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/></svg> Weekly Program Roll-Up Copilot</h1>
  <p>Turns the week's meetings, schedule, and action log into an exec-ready draft update —
     what's working, what's not, the hot topics. Numbers are computed; the AI writes the prose.</p>
</header>
<div class="wrap">
  <div class="tabs">
    <button class="tab {{ 'active' if tab=='input' else '' }}" onclick="show('input')">1 · Input</button>
    <button class="tab {{ 'active' if tab=='update' else '' }}" onclick="show('update')">2 · Weekly Update</button>
    <button class="tab {{ 'active' if tab=='trust' else '' }}" onclick="show('trust')">3 · Trust &amp; Evaluation</button>
  </div>

  <div id="input" class="panel {{ 'active' if tab=='input' else '' }}">
    <form method="post" enctype="multipart/form-data">
      <div class="card">
        <h3>Data source</h3>
        <div class="seg" role="tablist">
          <input type="radio" id="src-sample" name="source" value="sample" checked onchange="upRows(false)">
          <label for="src-sample" class="seg-btn"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5"/><path d="M3 12c0 1.7 4 3 9 3s9-1.3 9-3"/></svg> Use sample data</label>
          <input type="radio" id="src-upload" name="source" value="upload" onchange="upRows(true)">
          <label for="src-upload" class="seg-btn"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M17 8l-5-5-5 5M12 3v12"/></svg> Upload my own</label>
        </div>

        <div id="sampleNote" class="note">
          A synthetic mixed-signal program week — <b>4 workstreams, 4 milestones, 6 actions, 3 meeting notes</b>.
          Nothing proprietary; safe to demo.
        </div>

        <div id="uprows" class="uprows">
          <div class="up-grid">
            <label class="drop" for="f-ws"><span class="drop-t">Workstreams CSV</span>
              <span class="drop-h" id="n-ws">Choose file…</span>
              <input id="f-ws" type="file" name="workstreams" accept=".csv" onchange="fname(this,'n-ws')"></label>
            <label class="drop" for="f-ms"><span class="drop-t">Milestones CSV</span>
              <span class="drop-h" id="n-ms">Choose file…</span>
              <input id="f-ms" type="file" name="milestones" accept=".csv" onchange="fname(this,'n-ms')"></label>
            <label class="drop" for="f-al"><span class="drop-t">Action-log CSV</span>
              <span class="drop-h" id="n-al">Choose file…</span>
              <input id="f-al" type="file" name="actions" accept=".csv" onchange="fname(this,'n-al')"></label>
            <label class="drop" for="f-mt"><span class="drop-t">Meeting notes (.txt)</span>
              <span class="drop-h" id="n-mt">Choose several…</span>
              <input id="f-mt" type="file" name="meetings" accept=".txt" multiple onchange="fname(this,'n-mt')"></label>
          </div>
          <div class="tpl">Need the format? Download a sample template:
            <a href="/sample/workstreams.csv" download>workstreams.csv</a> ·
            <a href="/sample/milestones.csv" download>milestones.csv</a> ·
            <a href="/sample/action_log.csv" download>action_log.csv</a> ·
            <a href="/sample/meeting.txt" download>meeting note .txt</a>
            <div class="tpl-h">Any field you leave out falls back to the bundled sample, so you can try one file at a time.</div>
          </div>
        </div>
      </div>

      <div class="card">
        <h3>Engine</h3>
        <label class="switch-row" for="use_llm">
          <span class="sw"><input type="checkbox" id="use_llm" name="use_llm" checked><span class="sw-track"></span></span>
          <span><b>Use Claude to mine meetings &amp; write the update</b><br>
            <span class="hint">Extracts actions, decisions, and hot topics from the notes, then drafts the prose.
              Turn off for a fully deterministic, schedule-only draft — no API key needed.</span></span>
        </label>
        <button class="go" type="submit"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg> Generate weekly update</button>
      </div>
    </form>
  </div>

  <div id="update" class="panel {{ 'active' if tab=='update' else '' }}">
    {% if result_html %}{{ result_html|safe }}{% else %}
      <div class="card"><p class="lead">No update yet.</p>
        <p class="hint">Go to <b>Input</b> and click “Generate weekly update”.</p></div>
    {% endif %}
  </div>

  <div id="trust" class="panel {{ 'active' if tab=='trust' else '' }}">
    <div class="card"><h3>How this stays trustworthy</h3>
      <p class="lead">The judgment-bearing numbers — every RAG color, action age, hot-topic rank — are
        computed in plain Python and checked against hand-labeled scenarios. The AI only reads the messy
        meeting notes and writes the prose. These are the dimensions the eval harness scores:</p></div>
    {% for f in framework %}
      <div class="fr"><h4>{{f.term}}</h4><div class="g">{{f.guarantee}}</div>
        <div class="wy">{{f.why}}</div><div class="hw"><b>How it's proven:</b> {{f.how}}</div></div>
    {% endfor %}
    <div class="card"><h3>Run it yourself</h3>
      <p class="hint">Offline (deterministic, free): <span class="mono">python evals/eval_harness.py</span><br>
        Against live Claude (adds grounding + recall): <span class="mono">python evals/eval_harness.py --llm</span></p></div>
  </div>
</div>

<textarea id="basemd" style="display:none">{{ markdown }}</textarea>
<script>
function show(t){document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));
  document.getElementById(t).classList.add('active');
  event.target.classList.add('active');}
function upRows(s){document.getElementById('uprows').classList.toggle('show',s);
  document.getElementById('sampleNote').style.display=s?'none':'block';}
function fname(inp,id){let el=document.getElementById(id);let drop=inp.closest('.drop');
  if(inp.files.length){el.textContent=inp.files.length>1?inp.files.length+" files selected":inp.files[0].name;
    drop.classList.add('filled');}
  else{el.textContent="Choose file…";drop.classList.remove('filled');}}
function assembleMd(){let md=document.getElementById('basemd').value;
  let takes=[...document.querySelectorAll('.take')].filter(t=>t.value.trim());
  if(takes.length){md+="\\n## Your take\\n"+takes.map(t=>"**"+t.dataset.prompt+"** "+t.value.trim()).join("\\n\\n")+"\\n";}
  return md;}
function copyMd(){navigator.clipboard.writeText(assembleMd()).then(()=>{
  let c=document.getElementById('copied');c.textContent="Copied — paste into email / Teams / Confluence";
  setTimeout(()=>c.textContent="",4000);});}
function downloadMd(){let b=new Blob([assembleMd()],{type:"text/markdown"});
  let a=document.createElement("a");a.href=URL.createObjectURL(b);a.download="weekly_update.md";a.click();}
</script>
</body></html>"""

@app.route("/", methods=["GET", "POST"])
def index():
    global LAST
    tab = "input"
    if request.method == "POST":
        try:
            LAST = run_rollup(request.form, request.files)
        except Exception as e:                       # API error, bad upload, etc.
            LAST = {"error": str(e) or e.__class__.__name__}
        tab = "update"

    result_html = markdown = ""
    if LAST and LAST.get("error"):
        result_html = (
            '<div class="card warn"><h3>Could not generate the update</h3>'
            f'<p class="lead">{esc(LAST["error"])}</p>'
            '<p class="hint">Tip: turn off <b>Use Claude</b> on the Input tab for a deterministic '
            'draft that needs no API key, or add your <code>ANTHROPIC_API_KEY</code> to a '
            '<code>.env</code> file and try again.</p></div>')
    elif LAST and LAST.get("update"):
        banner = (f'<div class="note" style="margin-bottom:16px">{esc(LAST["notice"])}</div>'
                  if LAST.get("notice") else "")
        result_html = banner + render_update(LAST["update"])
        markdown = LAST["markdown"]

    return render_template_string(
        PAGE, tab=tab, result_html=result_html, markdown=markdown, framework=FRAMEWORK)


@app.route("/sample/<path:name>")
def sample_file(name):
    """Serve the bundled sample CSVs (and one meeting note) as downloadable templates."""
    from flask import abort, send_file
    allowed = {
        "workstreams.csv": os.path.join(SAMPLE, "workstreams.csv"),
        "milestones.csv": os.path.join(SAMPLE, "milestones.csv"),
        "action_log.csv": os.path.join(SAMPLE, "action_log.csv"),
        "meeting.txt": os.path.join(SAMPLE, "meetings", "2026-06-12_program_staff.txt"),
    }
    path = allowed.get(name)
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=name)


if __name__ == "__main__":
    print("Weekly Roll-Up Copilot UI -> http://127.0.0.1:5000")
    app.run(debug=False, port=5000)
