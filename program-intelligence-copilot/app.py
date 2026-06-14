"""Program Intelligence Copilot — web UI (Flask).

Two things the CLI does, made clickable:

  * Ask the Copilot — fast, retrieval-augmented, cited Q&A over the whole program history.
  * Weekly Update  — runs the full multi-agent graph (planner → analysts → risk → critic →
                     synthesizer) and renders the cited, trend-aware update, including the
                     claims the critic rejected as ungrounded.

A thin front end over the same modules the CLI uses (retriever / ask / orchestrator). Pure
Flask + inline SVG (no JS libs), consistent with the rest of the portfolio. Flask is run
threaded so the (slow) full-graph run doesn't block the Ask tab.

Run:
    python app.py     →  http://127.0.0.1:5001
"""

import os
import sys

from flask import Flask, render_template_string, request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ask as ask_mod          # noqa: E402
import orchestrator            # noqa: E402
import status_core             # noqa: E402
from retriever import get_retriever   # noqa: E402

app = Flask(__name__)

LAST_ASK = None     # {question, answer, cites, mode}
LAST_UPDATE = None  # ProgramWeeklyUpdate

RAG = {"green": ("#15803d", "#dcfce7"), "amber": ("#b45309", "#fef3c7"),
       "red": ("#b91c1c", "#fee2e2"), "unknown": ("#475569", "#e2e8f0")}
TOPIC = {"persistent": ("#b91c1c", "#fee2e2"), "emerging": ("#b45309", "#fef3c7"),
         "resolving": ("#15803d", "#dcfce7")}

EXAMPLES = [
    "How has the channel-B audio THD issue evolved, and is it blocking the gate?",
    "Why is EVT slipping?",
    "What is required to exit the EVT phase gate?",
    "Show me the ATE program timeline",
]


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def chip(text, fg, bg):
    return f'<span class="chip" style="color:{fg};background:{bg}">{esc(text)}</span>'


def has_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #

def render_ask(state):
    if not state:
        return ('<div class="card"><p class="lead">Ask anything about the program’s history.</p>'
                '<p class="hint">The copilot retrieves evidence across every week and answers '
                'with citations — it only uses what it can cite.</p></div>')
    cites = state["cites"]
    src = "".join(
        f'<div class="src-row"><span class="src-n">[{i}]</span>'
        f'<div><b>{esc(c.label())}</b> {chip(c.source_type, "#27406e", "#eef4ff")}'
        f'<div class="src-txt">{esc(c.snippet)}</div></div></div>'
        for i, c in enumerate(cites, 1))
    body = []
    body.append(f'<div class="card"><div class="q">{esc(state["question"])}</div>'
                f'<div class="rmode">retrieval: {esc(state["mode"])} · {len(cites)} sources</div></div>')
    if state.get("answer"):
        ans = esc(state["answer"]).replace("\n", "<br>")
        body.append(f'<div class="card answer">{ans}</div>')
    else:
        body.append('<div class="card warn"><b>Retrieve-only mode</b> (no ANTHROPIC_API_KEY). '
                    'Showing the retrieved sources; add a key for a written, cited answer.</div>')
    body.append(f'<div class="card"><h3>Sources</h3>{src}</div>')
    return "".join(body)


def _kpi_row(u):
    reds = sum(1 for a in u.workstreams if a.rag == "red")
    ambers = sum(1 for a in u.workstreams if a.rag == "amber")
    greens = sum(1 for a in u.workstreams if a.rag == "green")
    actions = status_core.get_open_actions()
    blocked = sum(1 for a in actions if a["state"] in ("blocked", "slipped"))
    max_slip = max((m["slip_days"] for m in status_core.get_schedule()), default=0)
    sources = sum(len(a.evidence) for a in u.workstreams)
    ofg, _ = RAG.get(u.overall_rag, RAG["unknown"])
    boxes = [
        ("Overall", u.overall_rag.upper(), ofg),
        ("Workstreams", f'<span style="color:#b91c1c">{reds}</span>·'
                        f'<span style="color:#b45309">{ambers}</span>·'
                        f'<span style="color:#15803d">{greens}</span>', None),
        ("Blocked / slipped", str(blocked), "#b91c1c" if blocked else None),
        ("Max slip", f"{max_slip}d", "#b45309" if max_slip else None),
        ("Hot topics", str(len(u.hot_topics)), None),
        ("Sources cited", str(sources), None),
    ]
    cells = ""
    for label, val, color in boxes:
        style = f' style="color:{color}"' if color else ""
        cells += f'<div class="kpi"><div class="kpi-n"{style}>{val}</div><div class="kpi-l">{label}</div></div>'
    return f'<div class="kpis">{cells}</div>'


def _trust_modal(u):
    cited = sum(len(a.evidence) for a in u.workstreams)
    n_rej = len(u.rejected_claims)
    rej = "".join(f'<li><b>[{esc(ws)}]</b> {esc(t)}<div class="hint">{esc(r)}</div></li>'
                  for ws, t, r in u.rejected_claims) or '<li class="muted">none — all claims were grounded</li>'
    trend = (f'<div class="tm-sec"><h4>Trend (how the program moved)</h4>'
             f'<p class="hint">{esc(u.trend_summary)}</p></div>' if u.trend_summary else "")
    return f"""
    <div id="trustModal" class="ov" onclick="closeTrust(event)">
      <div class="modal" onclick="event.stopPropagation()">
        <div class="modal-h"><b>Why you can trust this update</b>
          <button class="x" onclick="closeTrust()" aria-label="Close">&times;</button></div>
        <p class="hint">Every response from this copilot runs through the same framework and harness.</p>
        <div class="fw-grid">
          <div class="fwc"><div class="fwc-t">Deterministic numbers</div>
            RAG colors and the overall rollup are computed in Python; agents adopt them via the
            <span class="mono">compute_rag</span> tool — never guessed.</div>
          <div class="fwc"><div class="fwc-t">Grounded retrieval</div>
            Hybrid BM25 + vector search; every claim cites the source chunks it came from.</div>
          <div class="fwc"><div class="fwc-t">Adversarial critic</div>
            A separate agent re-checks every claim against its cited sources and removes the
            unsupported ones before you see them.</div>
          <div class="fwc"><div class="fwc-t">Evaluation harness</div>
            Retrieval recall, status determinism, and critic grounding — <b>14/14</b> on the
            labelled gold set.</div>
        </div>
        <div class="tm-sec"><h4>Applied to this update</h4>
          <div class="tm-stats">
            <span>{cited} sources cited</span>
            <span>{n_rej} claim(s) rejected by the critic</span>
            <span>overall RAG = deterministic rollup</span>
            <span>confidence: {esc(u.confidence)}</span>
          </div></div>
        <div class="tm-sec"><h4>Claims the critic rejected as ungrounded</h4>
          <ul class="flat">{rej}</ul></div>
        {trend}
      </div>
    </div>"""


def render_update(u):
    if not u:
        return ('<div class="card"><p class="lead">No update generated yet.</p>'
                '<p class="hint">Click <b>Run the agent graph</b> below. It runs the planner, a '
                'workstream analyst per workstream (in parallel), a risk agent, an adversarial '
                'critic, and a synthesizer — about 1–2 minutes.</p></div>')
    fg, bg = RAG.get(u.overall_rag, RAG["unknown"])
    shield = ('<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" '
              'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
              '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>')
    out = [f"""<div class="banner" style="background:{bg};border-color:{fg}">
      <div><div class="b-rag" style="color:{fg}">{u.overall_rag.upper()}</div>
        <div class="b-head">{esc(u.headline)}</div></div>
      <div class="b-right">{chip('confidence: ' + u.confidence, '#fff', fg)}
        <button class="trust-btn" onclick="openTrust()">{shield} Why trust this</button></div>
    </div>"""]

    out.append(_kpi_row(u))

    bullets = "".join(f"<li>{esc(b)}</li>" for b in u.executive_summary) or "<li>—</li>"
    out.append(f'<div class="card"><h3>Executive summary</h3><ul class="exec">{bullets}</ul></div>')

    cards = []
    for a in u.workstreams:
        wf, wb = RAG.get(a.rag, RAG["unknown"])
        top_issue = (f'<div class="ws-issue">{esc(a.whats_not[0])}</div>' if a.whats_not else "")
        more_issues = "".join(f'<li class="bad">{esc(x)}</li>' for x in a.whats_not[1:])
        work = "".join(f'<li class="ok">{esc(x)}</li>' for x in a.whats_working)
        details = ""
        if more_issues or work:
            details = (f'<details><summary>more · {len(a.evidence)} sources</summary>'
                       + (f'<ul>{more_issues}</ul>' if more_issues else "")
                       + (f'<div class="col-t ok-t">Working</div><ul>{work}</ul>' if work else "")
                       + '</details>')
        else:
            details = f'<div class="ev">{len(a.evidence)} cited sources</div>'
        cards.append(f"""<div class="ws" style="border-top:4px solid {wf}">
          <div class="ws-h"><b>{esc(a.workstream)}</b>{chip(a.rag.upper(), wf, wb)}</div>
          <p class="ws-sum clamp">{esc(a.summary)}</p>{top_issue}{details}</div>""")
    out.append(f'<h3 class="sec">Workstreams</h3><div class="ws-grid">{"".join(cards)}</div>')

    if u.hot_topics:
        rows = ""
        for t in u.hot_topics:
            tf, tb = TOPIC.get(t.status, ("#475569", "#e2e8f0"))
            rows += (f'<div class="hot"><div>{chip(t.status, tf, tb)}</div>'
                     f'<div><b>{esc(t.topic)}</b><div class="hot-why clamp">{esc(t.why)}</div></div></div>')
        out.append(f'<div class="card"><h3>Hot topics</h3>{rows}</div>')
    if u.asks:
        items = "".join(f'<li>{esc(x)}</li>' for x in u.asks)
        out.append(f'<div class="card"><h3>Asks for leadership</h3><ul class="flat">{items}</ul></div>')

    out.append(_trust_modal(u))
    return "".join(out)


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Program Intelligence Copilot</title>
<link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap" rel=stylesheet>
<style>
:root{--primary:#1e40af;--accent:#b45309;--bg:#f4f7fb;--surface:#fff;--ink:#0f1f3d;--mut:#5b6b86;--line:#dbe4f0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:"Fira Sans",system-ui,sans-serif;line-height:1.5}
.mono{font-family:"Fira Code",monospace}
header{background:linear-gradient(120deg,#0f1f3d,#1e40af);color:#fff;padding:22px 30px}
header h1{margin:0;font-size:1.3rem;display:flex;gap:11px;align-items:center}
header p{margin:4px 0 0;color:#c7d6f0;font-size:.9rem;max-width:820px}
.wrap{max-width:1060px;margin:0 auto;padding:22px}
.tabs{display:flex;gap:4px;border-bottom:2px solid var(--line);margin-bottom:20px;flex-wrap:wrap}
.tab{padding:10px 18px;border:none;background:none;color:var(--mut);font-weight:600;font-size:.92rem;cursor:pointer;border-bottom:3px solid transparent;margin-bottom:-2px;font-family:inherit;text-decoration:none}
.tab:hover{color:var(--ink)}.tab.active{color:var(--primary);border-bottom-color:var(--accent)}
.panel{display:none}.panel.active{display:block}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:16px;box-shadow:0 1px 2px rgba(15,31,61,.04)}
.card h3{margin:0 0 12px;font-size:1rem}.sec{margin:20px 0 12px}
.lead{font-size:1.02rem;margin:0}.hint{color:var(--mut);font-size:.82rem;font-weight:400}
.chip{display:inline-block;padding:2px 10px;border-radius:99px;font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.03em}
.qform{display:flex;gap:10px;margin-bottom:16px}
.qform input[type=text]{flex:1;padding:12px 14px;border:1px solid var(--line);border-radius:10px;font-family:inherit;font-size:.95rem}
.btn{background:var(--primary);color:#fff;border:none;padding:12px 22px;border-radius:10px;font-weight:700;cursor:pointer;font-family:inherit;font-size:.92rem;display:inline-flex;gap:8px;align-items:center}
.btn:hover{background:#1b3aa3}.btn.amber{background:var(--accent)}.btn.amber:hover{background:#92400e}
.ex{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.ex a{font-size:.82rem;color:var(--primary);background:#eef4ff;border:1px solid #c7d6f0;border-radius:99px;padding:5px 12px;text-decoration:none}
.q{font-weight:600;font-size:1.05rem}.rmode{color:var(--mut);font-size:.8rem;margin-top:4px}
.answer{white-space:normal;line-height:1.65}
.src-row{display:flex;gap:10px;padding:9px 0;border-bottom:1px solid var(--line)}.src-row:last-child{border:none}
.src-n{color:var(--accent);font-weight:700;font-family:"Fira Code",monospace}
.src-txt{color:var(--mut);font-size:.85rem;margin-top:3px}
.banner{display:flex;justify-content:space-between;align-items:center;border:2px solid;border-radius:14px;padding:16px 20px;margin-bottom:14px;gap:14px}
.b-rag{font-size:1.5rem;font-weight:800}.b-head{font-weight:500;margin-top:2px;max-width:660px;font-size:.96rem}
.b-right{display:flex;flex-direction:column;gap:8px;align-items:flex-end;flex:0 0 auto}
.trust-btn{display:inline-flex;align-items:center;gap:6px;background:#fff;border:1px solid var(--line);color:var(--primary);font-weight:600;font-size:.8rem;padding:6px 11px;border-radius:8px;cursor:pointer;font-family:inherit;white-space:nowrap}
.trust-btn:hover{background:#eef4ff}
.kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-bottom:16px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:13px 10px;text-align:center}
.kpi-n{font:700 1.5rem "Fira Code",monospace;line-height:1.1}
.kpi-l{color:var(--mut);font-size:.72rem;margin-top:4px}
ul.exec{margin:0;padding-left:20px}ul.exec li{margin:5px 0;font-size:.95rem}
.ws-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.ws{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:13px 15px}
.ws-h{display:flex;justify-content:space-between;align-items:center;gap:8px}
.ws-sum{color:var(--mut);font-size:.86rem;margin:5px 0 8px}
.clamp{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.ws-issue{font-size:.85rem;color:#b91c1c;padding-left:14px;position:relative}
.ws-issue::before{content:"!";position:absolute;left:0;font-weight:700}
.col-t{font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;margin:8px 0 2px}
.ok-t{color:#15803d}
.ws details{margin-top:8px}.ws summary{cursor:pointer;color:var(--primary);font-size:.78rem;font-weight:600;font-family:"Fira Code",monospace}
.ws ul{margin:6px 0 0;padding-left:16px}.ws li{font-size:.83rem;margin:3px 0}
li.ok::marker{content:"✓ ";color:#15803d}li.bad::marker{content:"! ";color:#b91c1c}.muted{color:var(--mut);list-style:none;margin-left:-12px}
.ev{margin-top:8px;font-size:.74rem;color:var(--mut);font-family:"Fira Code",monospace}
.hot{display:flex;gap:12px;align-items:flex-start;padding:9px 0;border-bottom:1px solid var(--line)}.hot:last-child{border:none}
.hot-why{color:var(--mut);font-size:.85rem;margin-top:2px}
ul.flat{margin:0;padding-left:18px}ul.flat li{margin:6px 0;font-size:.9rem}
.warn{background:#fffbeb;border-color:#fde68a}
/* Trust modal */
.ov{display:none;position:fixed;inset:0;background:rgba(15,31,61,.5);z-index:100;padding:24px;overflow:auto}
.ov.open{display:flex;align-items:flex-start;justify-content:center}
.modal{background:#fff;border-radius:16px;max-width:680px;width:100%;margin:auto;padding:22px 24px;box-shadow:0 20px 60px rgba(0,0,0,.3)}
.modal-h{display:flex;justify-content:space-between;align-items:center;font-size:1.1rem;margin-bottom:4px}
.modal-h .x{border:none;background:none;font-size:1.6rem;line-height:1;color:var(--mut);cursor:pointer}
.fw-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:14px 0}
.fwc{border:1px solid var(--line);border-radius:10px;padding:11px 13px;font-size:.84rem;color:var(--mut)}
.fwc-t{color:var(--ink);font-weight:700;font-size:.88rem;margin-bottom:3px}
.tm-sec{margin-top:14px}.tm-sec h4{margin:0 0 6px;font-size:.92rem}
.tm-stats{display:flex;flex-wrap:wrap;gap:8px}
.tm-stats span{background:#eef4ff;color:#27406e;border-radius:99px;padding:4px 11px;font-size:.8rem;font-weight:600}
.note{background:#eef4ff;border:1px solid #c7d6f0;border-radius:10px;padding:12px 14px;font-size:.86rem;color:#27406e;margin-bottom:14px}
.fr{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px;background:#fff}
.fr h4{margin:0 0 4px;color:var(--primary)}.spin{color:var(--mut);font-size:.85rem}
a.tab:focus-visible,.btn:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
@media(max-width:720px){.ws-grid{grid-template-columns:1fr}}
</style></head><body>
<header>
  <h1><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v3M5.6 5.6l2.1 2.1M3 12h3M18 12h3M16.3 7.7l2.1-2.1M12 8a4 4 0 0 0-2 7.5V18h4v-2.5A4 4 0 0 0 12 8zM10 21h4"/></svg> Program Intelligence Copilot</h1>
  <p>Retrieval-augmented, multi-agent program analyst. Ask across the whole program history, or
     run the agent graph to produce a cited, trend-aware weekly update. Numbers are computed; the
     agents retrieve, reason, and are checked by an adversarial critic.</p>
</header>
<div class="wrap">
  <div class="tabs">
    <a class="tab {{ 'active' if tab=='ask' else '' }}" href="/?tab=ask">1 · Ask the Copilot</a>
    <a class="tab {{ 'active' if tab=='update' else '' }}" href="/?tab=update">2 · Weekly Update</a>
    <a class="tab {{ 'active' if tab=='trust' else '' }}" href="/?tab=trust">3 · Trust &amp; Evaluation</a>
  </div>

  <div class="panel {{ 'active' if tab=='ask' else '' }}">
    <form class="qform" method="post" action="/ask">
      <input type="text" name="q" placeholder="Ask about the program history…" value="{{ ask_q }}" autofocus>
      <button class="btn" type="submit">Ask</button>
    </form>
    <div class="ex">
      {% for e in examples %}<a href="/ask?q={{ e|urlencode }}">{{ e }}</a>{% endfor %}
    </div>
    {{ ask_html|safe }}
  </div>

  <div class="panel {{ 'active' if tab=='update' else '' }}">
    <div class="note"><b>Runs the full agent graph.</b> Planner → a workstream analyst per
      workstream (parallel) → risk agent → adversarial critic → synthesizer. Takes ~1–2 minutes
      and makes ~10+ Claude calls (needs <span class="mono">ANTHROPIC_API_KEY</span> and costs a
      little API credit). Exploring for free? The <b>Ask</b> tab works in retrieve-only mode and
      the evals run offline — neither needs a key.</div>
    <form method="post" action="/run" onsubmit="document.getElementById('rb').disabled=true;document.getElementById('rb').textContent='Running the agents…'">
      <button id="rb" class="btn amber" type="submit">Run the agent graph</button>
    </form>
    <div style="height:14px"></div>
    {{ update_html|safe }}
  </div>

  <div class="panel {{ 'active' if tab=='trust' else '' }}">
    <div class="card"><h3>How this stays trustworthy</h3>
      <p class="lead">The judgment-bearing numbers are computed in Python and the agents must call
      them as tools; every narrative claim must cite a retrieved source, and an adversarial critic
      strips any claim its sources don’t support before synthesis.</p></div>
    <div class="fr"><h4>Determinism</h4><div>RAG colors and the overall rollup come from
      <span class="mono">status_core</span>, never an LLM. Agents adopt them via the
      <span class="mono">compute_rag</span> tool.</div></div>
    <div class="fr"><h4>Grounded retrieval</h4><div>Hybrid BM25 + (optional) Voyage vectors;
      every answer and assessment cites the chunks it used.</div></div>
    <div class="fr"><h4>Adversarial grounding</h4><div>The critic verifies each claim against its
      cited sources and rejects the unsupported ones — shown in the update’s “rejected” section.</div></div>
    <div class="card"><h3>Run the evals</h3>
      <p class="hint">Offline (free): <span class="mono">python evals/eval_harness.py</span> → 12/12<br>
        With the live critic test: <span class="mono">python evals/eval_harness.py --llm</span> → 14/14</p></div>
  </div>
</div>
<script>
function openTrust(){var m=document.getElementById('trustModal');if(m)m.classList.add('open');}
function closeTrust(e){var m=document.getElementById('trustModal');if(m)m.classList.remove('open');}
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeTrust();});
</script>
</body></html>"""


@app.route("/")
def index():
    tab = request.args.get("tab", "ask")
    return render_template_string(
        PAGE, tab=tab, examples=EXAMPLES,
        ask_q=(LAST_ASK or {}).get("question", ""),
        ask_html=render_ask(LAST_ASK), update_html=render_update(LAST_UPDATE))


@app.route("/ask", methods=["GET", "POST"])
def do_ask():
    global LAST_ASK
    q = (request.values.get("q") or "").strip()
    if q:
        r = get_retriever()
        cites = r.search(q)
        answer = ask_mod.answer(q, cites) if has_key() and cites else None
        LAST_ASK = {"question": q, "answer": answer, "cites": cites, "mode": r.mode}
    return render_template_string(
        PAGE, tab="ask", examples=EXAMPLES, ask_q=q,
        ask_html=render_ask(LAST_ASK), update_html=render_update(LAST_UPDATE))


@app.route("/run", methods=["POST"])
def do_run():
    global LAST_UPDATE
    banner = ""
    if not has_key():
        banner = ('<div class="card warn"><h3>An API key is needed for the agent graph</h3>'
                  '<p>The multi-agent run calls Claude. Set <span class="mono">ANTHROPIC_API_KEY</span> '
                  'in a <span class="mono">.env</span> file and try again.</p>'
                  '<p class="hint">No key? The <b>Ask the Copilot</b> tab works in retrieve-only mode, '
                  'and <span class="mono">python evals/eval_harness.py</span> runs free — both need no key.</p></div>')
    else:
        try:
            LAST_UPDATE = orchestrator.run_program_update(parallel=True)
        except Exception as e:
            banner = (f'<div class="card warn"><h3>The run failed</h3><p>{esc(str(e) or type(e).__name__)}</p>'
                      '<p class="hint">Check your key and that <span class="mono">python ingest.py</span> '
                      'has been run, then try again.</p></div>')
    return render_template_string(
        PAGE, tab="update", examples=EXAMPLES, ask_q=(LAST_ASK or {}).get("question", ""),
        ask_html=render_ask(LAST_ASK), update_html=banner + render_update(LAST_UPDATE))


if __name__ == "__main__":
    print("Program Intelligence Copilot UI -> http://127.0.0.1:5001")
    app.run(debug=False, port=5001, threaded=True)
