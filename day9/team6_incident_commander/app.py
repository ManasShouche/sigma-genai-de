"""
Team 6 — Incident Commander
2:47 AM. Pipeline down. 11 transactions processed, then crash.
You have 15 minutes before the CEO wakes up.
"""

import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "shared"))

import streamlit as st
import streamlit.components.v1 as components
import duckdb
import json
import re
import time
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
from bedrock_helper import call_nova_lite, call_nova_pro

# ── CONFIG ───────────────────────────────────────────────────────────────────
DB_PATH      = os.path.join(os.path.dirname(__file__), "..", "shared", "sigma_platform.duckdb")
TIME_BUDGET  = 900  # 15 minutes in seconds

st.set_page_config(
    page_title="🚨 Incident Commander — Sigma DataTech",
    page_icon="🚨",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── GOOGLE FONTS ──────────────────────────────────────────────────────────────
st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;900&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
""", unsafe_allow_html=True)

# ── STACK TRACE (from shared sample_data) ────────────────────────────────────
STACK_TRACE = """Traceback (most recent call last):
  File "pipeline.py", line 134, in load_silver
    con.execute(
        "INSERT INTO silver_transactions VALUES (?, ?, ?, ?, ?)",
        [row["transaction_id"], row["amount"], row["status"],
         row["merchant_id"], row["transaction_date"]]
    )
duckdb.duckdb.ConstraintException: Constraint Error:
  Duplicate key "TXN012" violates primary key constraint on silver_transactions
  File "pipeline.py", line 89, in main
    load_silver(silver_rows)
  File "pipeline.py", line 156, in run_pipeline
    main()
RuntimeError: Pipeline failed at Silver load stage after processing 11 records
Timestamp: 2024-01-22 02:47:33 UTC
Environment: prod | Region: us-east-1 | Run ID: run_20240122_0247"""

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* ══════════════════════════════════════════════════════════
       PALETTE  —  Zinc + Violet  (Linear / shadcn / Vercel dark)
       ──────────────────────────────────────────────────────────
       Background   #09090b  zinc-950
       Surface      #18181b  zinc-900
       Surface-2    #1c1c21  slightly raised
       Border       #27272a  zinc-800
       Border-muted #3f3f46  zinc-700

       Commander    #a78bfa  violet-400   (brand / primary)
       Cmd-mid      #7c3aed  violet-700
       Alert        #f97316  orange-500   (active incident)
       Alert-light  #fb923c  orange-400
       Blue         #38bdf8  sky-400      (Round 1 info)
       Green        #4ade80  green-400    (Round 3 / success)
       Red          #f87171  red-400      (P1 / crash)

       Text         #fafafa  zinc-50
       Text-muted   #a1a1aa  zinc-400
       Text-dim     #52525b  zinc-600
    ══════════════════════════════════════════════════════════ */

    /* ── Global ── */
    html, body, [class*="css"], .stApp {
        background-color: #09090b !important;
        font-size: 19px !important;
    }
    html, body, [class*="css"], .stMarkdown, p, div, span, label {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
        color: #fafafa;
        font-size: 1rem;
    }
    h1, h2, h3, h4 {
        font-family: 'Inter', sans-serif !important;
        font-weight: 700 !important;
        color: #fafafa !important;
        letter-spacing: -0.4px;
    }

    /* ── Hero box ── */
    .hero-box {
        background: #18181b;
        border: 1px solid #27272a;
        border-top: 2px solid #a78bfa;
        border-radius: 12px;
        padding: 28px 32px 22px 32px;
        margin-bottom: 6px;
        position: relative;
        overflow: hidden;
    }
    .hero-box::before {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0; height: 80px;
        background: radial-gradient(ellipse at 30% 0%, #a78bfa0d 0%, transparent 70%);
        pointer-events: none;
    }
    .hero-eyebrow {
        font-size: 0.72em;
        font-weight: 600;
        color: #52525b;
        letter-spacing: 5px;
        text-transform: uppercase;
        margin-bottom: 10px;
    }
    .hero-title {
        font-size: 3.6em;
        font-weight: 900;
        color: #fafafa;
        letter-spacing: -2px;
        line-height: 1;
        margin: 0;
    }
    .hero-dot { color: #a78bfa; }
    .hero-rule {
        height: 1px;
        background: linear-gradient(90deg, #a78bfa55, #38bdf81a, transparent);
        margin: 16px 0 12px 0;
    }

    /* ── Incident status strip ── */
    .inc-strip {
        display: flex;
        align-items: center;
        gap: 10px;
        flex-wrap: wrap;
        padding: 10px 0 18px 0;
    }
    .inc-badge {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        background: #f9731614;
        border: 1px solid #f9731640;
        border-radius: 20px;
        padding: 4px 14px;
        font-size: 0.8em;
        font-weight: 700;
        color: #fb923c;
        letter-spacing: 1px;
        text-transform: uppercase;
    }
    .inc-dot {
        width: 6px; height: 6px;
        background: #f97316;
        border-radius: 50%;
        display: inline-block;
        animation: pulse 1.4s ease-in-out infinite;
    }
    @keyframes pulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50%       { opacity: 0.4; transform: scale(0.7); }
    }
    .inc-meta {
        font-size: 0.82em;
        color: #52525b;
        font-family: 'JetBrains Mono', monospace !important;
    }
    .inc-sep { color: #27272a; }

    /* ── Round section headers ── */
    .round-card {
        border-left: 3px solid;
        padding: 10px 16px;
        margin: 20px 0 10px 0;
        border-radius: 0 8px 8px 0;
        background: #18181b;
        display: flex;
        align-items: center;
        gap: 12px;
        border-top: 1px solid #27272a;
        border-bottom: 1px solid #27272a;
        border-right: 1px solid #27272a;
    }
    .round-card b   { font-size: 1.05em; font-weight: 700; color: #fafafa; }
    .round-card span { color: #52525b; font-size: 0.94em; }
    .r1 { border-left-color: #38bdf8; }
    .r2 { border-left-color: #f97316; }
    .r3 { border-left-color: #4ade80; }

    /* ── Severity badges ── */
    .badge {
        display: inline-block; padding: 5px 22px; border-radius: 6px;
        font-weight: 900; font-size: 1.4em; letter-spacing: 3px;
        font-family: 'Inter', sans-serif;
    }
    .p1 { background:#f8717112; color:#f87171; border:1px solid #f8717140; }
    .p2 { background:#f9731612; color:#fb923c; border:1px solid #f9731640; }
    .p3 { background:#4ade8012; color:#4ade80; border:1px solid #4ade8040; }

    /* ── Error anatomy card ── */
    .err-card {
        background: #0a0a0d;
        border: 1px solid #27272a;
        border-top: 3px solid #f87171;
        border-radius: 12px;
        padding: 26px 24px;
        margin-bottom: 4px;
    }
    .err-header {
        display: flex; align-items: center; gap: 10px; margin-bottom: 18px;
    }
    .err-type {
        background: #f8717115; border: 1px solid #f8717140;
        color: #f87171; font-size: 0.88em; font-weight: 700;
        letter-spacing: 1.2px; text-transform: uppercase;
        padding: 5px 16px; border-radius: 20px;
        font-family: 'JetBrains Mono', monospace;
    }
    .err-fatal {
        background: #f9731615; border: 1px solid #f9731640;
        color: #fb923c; font-size: 0.82em; font-weight: 700;
        letter-spacing: 1.2px; text-transform: uppercase;
        padding: 5px 13px; border-radius: 20px;
    }
    .err-msg {
        font-size: 1.08em; color: #fafafa; line-height: 1.7;
        margin-bottom: 22px; font-weight: 500;
    }
    .err-msg code {
        background: #27272a; color: #a78bfa;
        padding: 2px 9px; border-radius: 4px; font-size: 0.95em;
    }
    /* call chain */
    .chain { display: flex; flex-direction: column; gap: 0; margin-bottom: 22px; }
    .chain-step {
        display: flex; align-items: center; gap: 12px;
        background: #18181b; border: 1px solid #27272a;
        border-radius: 7px; padding: 11px 16px;
        font-family: 'JetBrains Mono', monospace; font-size: 0.9em;
        color: #a1a1aa;
    }
    .chain-step.crash {
        background: #f9731608; border-color: #f9731650;
        color: #fb923c;
    }
    .chain-arrow {
        text-align: center; color: #3f3f46; font-size: 0.85em;
        line-height: 1; padding: 3px 0 3px 18px;
    }
    .chain-file { color: #52525b; font-size: 0.93em; }
    .chain-fn   { color: #fafafa; font-weight: 600; }
    .chain-fn.crash-fn { color: #fb923c; }
    /* meta grid */
    .err-meta {
        display: grid; grid-template-columns: 1fr 1fr;
        gap: 10px;
    }
    .err-meta-item {
        background: #18181b; border: 1px solid #27272a;
        border-radius: 7px; padding: 10px 14px;
    }
    .err-meta-label {
        font-size: 0.74em; font-weight: 700; text-transform: uppercase;
        letter-spacing: 0.8px; color: #52525b; display: block;
        margin-bottom: 3px;
    }
    .err-meta-val {
        font-size: 0.9em; color: #a1a1aa;
        font-family: 'JetBrains Mono', monospace;
    }
    .err-meta-val.prod { color: #f87171; font-weight: 700; }

    /* ── Hypothesis boxes ── */
    .hyp {
        background: #18181b;
        border: 1px solid #27272a;
        border-radius: 10px;
        padding: 16px 18px;
        margin: 8px 0;
        font-size: 1.01em;
        line-height: 1.75;
        color: #a1a1aa;
    }
    .hyp b { font-weight: 600; font-size: 0.88em; text-transform: uppercase;
              letter-spacing: 0.7px; color: #71717a; }
    .hyp1 { border-left: 3px solid #38bdf8; }
    .hyp2 { border-left: 3px solid #f97316; }

    /* ── CEO box ── */
    .ceo {
        background: #0d0d12;
        border: 1px solid #a78bfa40;
        border-left: 3px solid #a78bfa;
        border-radius: 8px;
        padding: 18px 22px;
        font-size: 1.08em;
        font-style: italic;
        color: #c4b5fd;
        line-height: 1.8;
        margin: 12px 0;
    }

    /* ── Verdict panel ── */
    .verdict {
        background: #0d0f0d;
        border: 1px solid #4ade8030;
        border-top: 2px solid #4ade80;
        border-radius: 10px;
        padding: 22px;
        margin-top: 14px;
    }

    /* ── Timer ── */
    .timer-ok   { color:#4ade80; font-size:2.1em; font-weight:900;
                  font-family:'JetBrains Mono',monospace; }
    .timer-warn { color:#fb923c; font-size:2.1em; font-weight:900;
                  font-family:'JetBrains Mono',monospace; }
    .timer-crit { color:#f87171; font-size:2.1em; font-weight:900;
                  font-family:'JetBrains Mono',monospace;
                  animation: blink 0.8s step-end infinite; }
    @keyframes blink { 50% { opacity:0.3; } }

    /* ── Lock ── */
    .locked {
        background:#18181b; border:1px dashed #3f3f46;
        border-radius:8px; padding:12px 18px;
        color:#3f3f46; text-align:center; font-size:0.86em;
    }

    /* ── Streamlit widget overrides ── */
    .stButton > button {
        font-family:'Inter',sans-serif !important;
        font-weight:600 !important;
        border-radius:7px !important;
        border: 1px solid #3f3f46 !important;
        background: #18181b !important;
        color: #fafafa !important;
    }
    .stTextArea textarea, .stTextInput input {
        font-family:'Inter',sans-serif !important;
        background:#18181b !important;
        border-color:#27272a !important;
        color:#fafafa !important;
        border-radius:7px !important;
    }
    code, pre { font-family:'JetBrains Mono',monospace !important; }
    .stMetric label {
        font-family:'Inter',sans-serif !important;
        font-size:0.82em !important; font-weight:700 !important;
        text-transform:uppercase; letter-spacing:0.8px; color:#52525b !important;
    }
    [data-testid="metric-container"] {
        background:#18181b !important;
        border:1px solid #27272a !important;
        border-radius:9px !important;
        padding:14px 18px !important;
    }
    .stSidebar { background:#09090b !important; }
    .stSidebar [data-testid="stSidebarContent"] {
        background:#09090b !important;
        border-right: 1px solid #27272a !important;
    }
    hr { border-color:#27272a !important; }
    [data-testid="stSelectbox"] > div {
        background:#18181b !important;
        border-color:#27272a !important;
        border-radius:7px !important;
    }
</style>
""", unsafe_allow_html=True)

# ── SESSION STATE ─────────────────────────────────────────────────────────────
defaults = {
    "start_time":    time.time(),
    "r1_done":       False,
    "r1_result":     None,
    "r2_done":       False,
    "r2_result":     None,
    "query_history": [],
    "hint_level":    0,
    "verdict":       None,
    "pending_sql":   "",
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── DB CONNECTION ─────────────────────────────────────────────────────────────
@st.cache_resource
def get_conn():
    return duckdb.connect(DB_PATH, read_only=True)

conn = get_conn()

# ── HELPERS ───────────────────────────────────────────────────────────────────
def elapsed():
    return int(time.time() - st.session_state.start_time)

def timer_display():
    rem = TIME_BUDGET - elapsed()
    if rem <= 0:
        return "OVERTIME ⚠", "timer-crit"
    m, s = divmod(rem, 60)
    label = f"{m:02d}:{s:02d}"
    cls = "timer-crit" if rem < 120 else ("timer-warn" if rem < 300 else "timer-ok")
    return label, cls

def parse_json(text):
    """Extract first JSON object from model response."""
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return {"raw": text}

def severity_badge(sev):
    sev = sev.upper().strip()
    cls = sev.lower() if sev in ("P1","P2","P3") else "p1"
    return f'<span class="badge {cls}">{sev}</span>'

# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🚨 Incident Dashboard")
    st.markdown(f"`INC-20240122-0001`")
    st.markdown(f"**Declared:** 02:47:33 UTC")
    st.markdown(f"**Impact:** 50,471 txns stuck in Bronze")
    st.divider()

    # Timer
    tval, tcls = timer_display()
    st.markdown("**⏱ Time Remaining**")
    st.markdown(f'<p class="{tcls}">{tval}</p>', unsafe_allow_html=True)
    if st.button("🔄 Refresh Timer"):
        st.rerun()

    st.divider()
    st.markdown("**📋 Progress**")
    st.markdown(f"{'✅' if st.session_state.r1_done else '⬜'} Round 1 — First Responder")
    st.markdown(f"{'✅' if st.session_state.r2_done else '⬜'} Round 2 — Devil's Advocate")
    st.markdown(f"{'✅' if st.session_state.verdict  else '⬜'} Round 3 — Investigation")

    st.divider()
    st.markdown("**🗄 DuckDB Tables**")
    st.code(
        "bronze_transactions  (21 rows)\n"
        "silver_transactions  (14 rows)\n"
        "gold_merchant_performance\n"
        "gold_daily_summary\n"
        "merchants\n"
        "pipeline_versions",
        language="text"
    )

    if st.session_state.query_history:
        st.divider()
        st.markdown(f"**🔍 Queries run:** {len(st.session_state.query_history)}")

# ── TITLE + HEADER ────────────────────────────────────────────────────────────
st.markdown("""
<div class="hero-box">
  <div class="hero-eyebrow">Sigma DataTech &nbsp;·&nbsp; AI Ops Platform &nbsp;·&nbsp; Day 9</div>
  <div class="hero-title">Incident Commander<span class="hero-dot">.</span></div>
  <div class="hero-rule"></div>
  <div class="inc-strip">
    <span class="inc-badge"><span class="inc-dot"></span>&nbsp;Active</span>
    <span class="inc-meta">INC-20240122-0001</span>
    <span class="inc-sep">·</span>
    <span class="inc-meta">02:47 AM UTC</span>
    <span class="inc-sep">·</span>
    <span class="inc-meta">Crashed after 11 records</span>
    <span class="inc-sep">·</span>
    <span class="inc-meta">50,471 txns stuck in Bronze</span>
    <span class="inc-sep">·</span>
    <span class="inc-meta">CEO dashboard @ 08:00 IST</span>
    <span class="inc-sep">·</span>
    <span class="inc-meta">run_20240122_0247</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# ROUND 1 — AI FIRST RESPONDER
# ══════════════════════════════════════════════════════════════════════════════
st.markdown('<div class="round-card r1"><b>🔵 Round 1 — AI First Responder</b>&nbsp;&nbsp;<span>Nova Pro reads the error → declares severity, root cause, and immediate fix</span></div>', unsafe_allow_html=True)

col_trace, col_r1 = st.columns([1, 1], gap="medium")

with col_trace:
    st.markdown("**📋 Error Breakdown**")
    st.markdown("""
<div class="err-card">
  <div class="err-header">
    <span class="err-type">ConstraintException</span>
    <span class="err-fatal">⚡ Fatal</span>
  </div>

  <div class="err-msg">
    Duplicate key <code>"TXN012"</code> violates PRIMARY KEY constraint on
    <code>silver_transactions</code>
  </div>

  <div class="chain">
    <div class="chain-step">
      <span class="chain-file">pipeline.py:156</span>
      &nbsp;→&nbsp;
      <span class="chain-fn">run_pipeline()</span>
    </div>
    <div class="chain-arrow">↓</div>
    <div class="chain-step">
      <span class="chain-file">pipeline.py:89</span>
      &nbsp;→&nbsp;
      <span class="chain-fn">main()</span>
    </div>
    <div class="chain-arrow">↓</div>
    <div class="chain-step crash">
      <span class="chain-file">pipeline.py:134</span>
      &nbsp;→&nbsp;
      <span class="chain-fn crash-fn">load_silver()</span>
      &nbsp;&nbsp;💥 <strong>CRASH</strong>
    </div>
  </div>

  <div class="err-meta">
    <div class="err-meta-item">
      <span class="err-meta-label">Timestamp</span>
      <span class="err-meta-val">2024-01-22 02:47:33 UTC</span>
    </div>
    <div class="err-meta-item">
      <span class="err-meta-label">Environment</span>
      <span class="err-meta-val prod">prod</span>
    </div>
    <div class="err-meta-item">
      <span class="err-meta-label">Region</span>
      <span class="err-meta-val">us-east-1</span>
    </div>
    <div class="err-meta-item">
      <span class="err-meta-label">Run ID</span>
      <span class="err-meta-val">run_20240122_0247</span>
    </div>
    <div class="err-meta-item">
      <span class="err-meta-label">Stage</span>
      <span class="err-meta-val">Silver load</span>
    </div>
    <div class="err-meta-item">
      <span class="err-meta-label">Rows before crash</span>
      <span class="err-meta-val">11 / 50,482</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

    # ── Transaction processing line graph ─────────────────────────────────
    st.markdown("**📈 Pipeline processing — live at crash**")

    # Build cumulative processing curve
    # X = transaction sequence, Y = cumulative count processed into silver
    labels_ok    = [f"TXN{i:04d}" for i in range(1, 12)]   # TXN0001-TXN0011 (success)
    labels_crash = ["TXN0012"]                               # crash point
    labels_stuck = [f"TXN{i:04d}" for i in range(13, 22)]  # never made it

    x_ok    = list(range(1, 12))
    x_crash = [12]
    x_stuck = list(range(13, 22))

    y_ok    = list(range(1, 12))       # cumulative 1→11
    y_crash = [11]                     # stays at 11 (crash, no new row)
    y_stuck = [11] * len(x_stuck)     # flat — stuck at 11 forever

    fig = go.Figure()

    # Normal processing — violet/green line
    fig.add_trace(go.Scatter(
        x=x_ok + x_crash,
        y=y_ok + y_crash,
        mode="lines+markers",
        name="Processed to Silver ✅",
        line=dict(color="#4ade80", width=2.5),
        marker=dict(size=6, color="#4ade80"),
        hovertemplate="<b>%{text}</b><br>Silver rows: %{y}<extra></extra>",
        text=labels_ok + labels_crash,
    ))

    # Crash point — orange X marker (matches alert palette)
    fig.add_trace(go.Scatter(
        x=x_crash,
        y=y_crash,
        mode="markers",
        name="💥 Crash — TXN0012 duplicate PK",
        marker=dict(size=18, color="#f97316", symbol="x", line=dict(width=3, color="#fb923c")),
        hovertemplate="<b>💥 CRASH HERE</b><br>TXN0012 duplicate PK violation<extra></extra>",
    ))

    # Stuck / unprocessed — red dashed flat
    fig.add_trace(go.Scatter(
        x=[12] + x_stuck,
        y=[11] + y_stuck,
        mode="lines+markers",
        name="Stuck in Bronze ⏳ (50,471 rows)",
        line=dict(color="#f87171", width=2, dash="dash"),
        marker=dict(size=4, color="rgba(248,113,113,0.3)"),
        hovertemplate="<b>%{text}</b><br>STUCK — never reached Silver<extra></extra>",
        text=["TXN0012"] + [f"TXN{i:04d}+" for i in range(13, 22)],
    ))

    # Shaded crash zone
    fig.add_vrect(
        x0=11.7, x1=21.5,
        fillcolor="#f97316", opacity=0.05,
        layer="below", line_width=0,
        annotation_text="CRASH ZONE",
        annotation_position="top left",
        annotation_font=dict(color="#f97316", size=10),
    )

    # Crash vertical line
    fig.add_vline(x=12, line_color="#f97316", line_width=1.5, line_dash="dot")

    fig.update_layout(
        plot_bgcolor="#09090b",
        paper_bgcolor="#09090b",
        font=dict(family="Inter, sans-serif", color="#71717a", size=11),
        legend=dict(bgcolor="#09090b", bordercolor="#27272a", borderwidth=1,
                    font=dict(size=10), orientation="h", y=-0.25),
        xaxis=dict(
            title="Transaction sequence",
            gridcolor="#18181b", zeroline=False,
            tickvals=list(range(1, 22, 2)),
            ticktext=[f"TXN{i:04d}" for i in range(1, 22, 2)],
            tickfont=dict(size=9),
            title_font=dict(size=10),
        ),
        yaxis=dict(
            title="Cumulative Silver rows",
            gridcolor="#18181b", zeroline=False,
            range=[0, 15],
            title_font=dict(size=10),
        ),
        margin=dict(l=10, r=10, t=10, b=50),
        height=240,
        showlegend=True,
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

with col_r1:
    if not st.session_state.r1_done:
        st.markdown("**Nova Pro will declare:**")
        st.markdown("- **Severity** — P1 / P2 / P3\n- **Root cause hypothesis**\n- **Confidence %**\n- **Immediate fix** (actionable in <2 min)\n- **ETA to resolve**")
        st.markdown("")
        if st.button("🚨 Dispatch AI First Responder", type="primary", use_container_width=True):
            with st.spinner("Nova Pro analyzing error details..."):
                system = (
                    "You are a senior on-call data engineer responding to a 2:47 AM production incident. "
                    "Be direct and time-pressured. Revenue is stopped. Every second counts. "
                    "Respond ONLY as a JSON object — no preamble, no markdown. "
                    "Keys: severity (P1/P2/P3), severity_reasoning, root_cause, "
                    "confidence_pct (0-100), immediate_fix, eta_minutes"
                )
                user = f"""PRODUCTION INCIDENT — IMMEDIATE RESPONSE REQUIRED.

Stack trace:
{STACK_TRACE}

Context: Pipeline processes 50,482 daily transactions. Crashed after row 11.
Silver table has PRIMARY KEY on transaction_id.
Bronze loads TRANSACTIONS_CLEAN + TRANSACTIONS_DIRTY together.

Triage this now. JSON only."""
                try:
                    raw = call_nova_pro(system, user, max_tokens=800)
                    result = parse_json(raw)
                    st.session_state.r1_result = result
                    st.session_state.r1_done   = True
                    st.rerun()
                except Exception as e:
                    st.error(f"Bedrock error: {e}")
    else:
        r = st.session_state.r1_result or {}
        sev = r.get("severity", "P1")
        st.markdown(severity_badge(sev), unsafe_allow_html=True)
        st.markdown("")
        reasoning = r.get("severity_reasoning", r.get("raw", "—"))
        st.markdown(
            f'<div class="hyp hyp1">'
            f'<b>🔍 Root Cause (H1):</b><br>{r.get("root_cause","—")}<br><br>'
            f'<b>🔧 Immediate Fix:</b><br>{r.get("immediate_fix","—")}<br><br>'
            f'<b>⏱ ETA:</b> ~{r.get("eta_minutes","?")} min &nbsp;|&nbsp; '
            f'<b>Confidence:</b> {r.get("confidence_pct","?")}%'
            f'<details style="margin-top:14px">'
            f'<summary style="cursor:pointer;color:#64748b;font-size:0.9em;font-weight:600;'
            f'letter-spacing:0.5px;list-style:none;user-select:none">'
            f'▸ Show reasoning</summary>'
            f'<p style="margin-top:10px;color:#94a3b8;font-size:0.97em;line-height:1.7">'
            f'{reasoning}</p>'
            f'</details>'
            f'</div>',
            unsafe_allow_html=True,
        )

# ══════════════════════════════════════════════════════════════════════════════
# ROUND 2 — DEVIL'S ADVOCATE
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.markdown('<div class="round-card r2"><b>🔴 Round 2 — Devil\'s Advocate</b>&nbsp;&nbsp;<span>Nova Lite argues Round 1 is wrong — proposes an alternative root cause</span></div>', unsafe_allow_html=True)

if not st.session_state.r1_done:
    st.markdown('<div class="locked">🔒 Complete Round 1 first.</div>', unsafe_allow_html=True)
else:
    if not st.session_state.r2_done:
        st.markdown("Nova Lite will argue the Round 1 diagnosis is **wrong** and give an alternative hypothesis that also fits the stack trace.")
        if st.button("😈 Summon Devil's Advocate", use_container_width=True):
            with st.spinner("Nova Lite building counterargument..."):
                h1_cause = (st.session_state.r1_result or {}).get("root_cause", "")
                system = (
                    "You are a contrarian senior engineer who challenges first-responder diagnoses in production incidents. "
                    "Your job: propose a DIFFERENT root cause that also explains the exact same crash. "
                    "Respond ONLY as a JSON object. "
                    "Keys: alternative_hypothesis, why_h1_is_wrong, supporting_evidence_in_trace, "
                    "confidence_pct (0-100), alternative_fix"
                )
                user = f"""Same stack trace:
{STACK_TRACE}

Round 1 (Nova Pro) said: {h1_cause}

Argue that Round 1 is wrong. Give a DIFFERENT hypothesis that explains the same ConstraintException on TXN012.
Think carefully: could this be a source data issue? A pipeline design flaw? An upstream retry?
Cite specific lines in the stack trace. JSON only."""
                try:
                    raw = call_nova_lite(system, user, max_tokens=800)
                    result = parse_json(raw)
                    st.session_state.r2_result = result
                    st.session_state.r2_done   = True
                    st.rerun()
                except Exception as e:
                    st.error(f"Bedrock error: {e}")
    else:
        r1 = st.session_state.r1_result or {}
        r2 = st.session_state.r2_result or {}
        col_h1, col_h2 = st.columns(2, gap="medium")
        with col_h1:
            st.markdown(f'<div class="hyp hyp1">'
                        f'<b style="color:#00b4d8">H1 — Nova Pro ({r1.get("confidence_pct","?")}%)</b><br><br>'
                        f'{r1.get("root_cause","—")}'
                        f'</div>', unsafe_allow_html=True)
        with col_h2:
            st.markdown(f'<div class="hyp hyp2">'
                        f'<b style="color:#f72585">H2 — Nova Lite ({r2.get("confidence_pct","?")}%)</b><br><br>'
                        f'{r2.get("alternative_hypothesis","—")}'
                        f'</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="hyp">'
                    f'<b>🗡 Why H1 might be wrong:</b><br>{r2.get("why_h1_is_wrong","—")}<br><br>'
                    f'<b>🔧 H2 fix:</b> {r2.get("alternative_fix","—")}'
                    f'</div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# ROUND 3 — YOUR INVESTIGATION
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.markdown('<div class="round-card r3"><b>🟢 Round 3 — Your Investigation</b>&nbsp;&nbsp;<span>Query DuckDB · rule out one hypothesis · find the real root cause</span></div>', unsafe_allow_html=True)

if not st.session_state.r2_done:
    st.markdown('<div class="locked">🔒 Complete Round 2 first.</div>', unsafe_allow_html=True)
else:
    # ── Query templates ───────────────────────────────────────────────────────
    st.markdown("**⚡ Quick queries — click to load:**")

    TEMPLATES = {
        "Row counts":               "SELECT\n  (SELECT COUNT(*) FROM bronze_transactions) AS bronze,\n  (SELECT COUNT(*) FROM silver_transactions) AS silver",
        "TXN012 in bronze":         "SELECT * FROM bronze_transactions\nWHERE transaction_id = 'TXN012'",
        "Duplicates in bronze":     "SELECT transaction_id, COUNT(*) AS cnt\nFROM bronze_transactions\nGROUP BY transaction_id\nHAVING cnt > 1",
        "Silver vs bronze diff":    "SELECT b.transaction_id, b.amount, b.status\nFROM bronze_transactions b\nLEFT JOIN silver_transactions s USING (transaction_id)\nWHERE s.transaction_id IS NULL",
        "Pipeline versions":        "SELECT version, SUBSTRING(code,1,200) AS code_preview\nFROM pipeline_versions",
        "🔑 Crack the case":        "SELECT transaction_id, COUNT(*) AS appears_in_n_source_files,\n  MIN(status) AS status, MIN(merchant_id) AS merchant_id\nFROM bronze_transactions\nGROUP BY transaction_id\nHAVING COUNT(*) > 1",
    }

    btn_cols = st.columns(3)
    for i, (label, sql) in enumerate(TEMPLATES.items()):
        if btn_cols[i % 3].button(label, use_container_width=True):
            st.session_state.pending_sql = sql

    # ── SQL editor ───────────────────────────────────────────────────────────
    default_sql = st.session_state.pending_sql or "SELECT * FROM bronze_transactions LIMIT 10"
    query = st.text_area("**SQL Editor:**", value=default_sql, height=100)

    run_col, hint_col = st.columns([3, 1])
    run_clicked  = run_col.button("▶ Run Query", type="primary", use_container_width=True)
    hint_clicked = hint_col.button("💡 Hint", use_container_width=True)

    if hint_clicked:
        st.session_state.hint_level += 1

    if st.session_state.hint_level > 0:
        hints = [
            "💡 **Hint 1:** The crash is a PRIMARY KEY violation on `transaction_id`. Where else could TXN012 exist before the silver insert?",
            "💡 **Hint 2:** Check `bronze_transactions` directly for TXN012. How many rows come back?",
            "💡 **Hint 3:** Bronze loaded `TRANSACTIONS_CLEAN + TRANSACTIONS_DIRTY` together. Is TXN012 in both source lists?",
        ]
        level = min(st.session_state.hint_level, len(hints))
        for h in hints[:level]:
            st.info(h)

    if run_clicked:
        st.session_state.pending_sql = ""
        try:
            df = conn.execute(query).df()
            entry = {"sql": query, "rows": len(df), "ts": datetime.now().strftime("%H:%M:%S")}
            st.session_state.query_history.append(entry)
            st.success(f"✅ {len(df)} rows")
            st.dataframe(df, use_container_width=True)
        except Exception as e:
            st.error(f"Query error: {e}")

    # Query history
    if st.session_state.query_history:
        with st.expander(f"📋 Query history ({len(st.session_state.query_history)} queries run)"):
            for entry in reversed(st.session_state.query_history[-8:]):
                st.caption(f"🕐 {entry['ts']}  →  {entry['rows']} rows")
                st.code(entry["sql"], language="sql")

    # ── Final verdict ─────────────────────────────────────────────────────────
    st.divider()

    vcol_title, vcol_autofill = st.columns([3, 1])
    vcol_title.markdown("### 🏁 Declare Final Verdict")

    # Pre-fill correct answers
    CORRECT_ANSWERS = {
        "severity":       "P1",
        "correct_hyp":    "Neither — I found the real root cause",
        "what_ai_missed": "TXN012 exists in BOTH TRANSACTIONS_CLEAN and TRANSACTIONS_DIRTY — both were loaded into bronze, creating a duplicate row. Neither AI looked at the source data.",
        "real_cause":     "TXN012 was ingested into bronze_transactions twice — once from the clean feed and once from the dirty/retry feed. The pipeline has no bronze-level deduplication, so both rows reached the silver INSERT which has a PRIMARY KEY constraint on transaction_id. The second insert raised a ConstraintException and aborted the entire run.",
        "ceo_summary":    "Pipeline crashed 2:47AM — duplicate TXN012 across clean+retry feeds caused a PK collision in Silver. 50,471 transactions delayed ~15min. Fix: bronze dedup added. Pipeline restarted 3:02AM. Zero data lost.",
        "cracking_query": "SELECT transaction_id, COUNT(*) AS cnt\nFROM bronze_transactions\nGROUP BY transaction_id\nHAVING cnt > 1",
    }

    if vcol_autofill.button("📋 Auto-fill answers", use_container_width=True, help="Fills in the correct answers for demo purposes"):
        for k, v_val in CORRECT_ANSWERS.items():
            st.session_state[f"prefill_{k}"] = v_val
        st.rerun()

    v_col1, v_col2 = st.columns(2, gap="medium")
    with v_col1:
        sev_options = ["P1", "P2", "P3"]
        sev_default = sev_options.index(st.session_state.get("prefill_severity", "P1"))
        final_severity = st.selectbox("Final severity", sev_options, index=sev_default)

        hyp_options = ["H1 — Nova Pro", "H2 — Nova Lite", "Neither — I found the real root cause"]
        hyp_default = st.session_state.get("prefill_correct_hyp", "Neither — I found the real root cause")
        hyp_idx = hyp_options.index(hyp_default) if hyp_default in hyp_options else 2
        correct_hyp = st.radio("Which hypothesis was correct?", hyp_options, index=hyp_idx)

        what_ai_missed = st.text_input(
            "What did BOTH AIs miss?",
            value=st.session_state.get("prefill_what_ai_missed", ""),
            placeholder="The thing neither mentioned...",
        )

    with v_col2:
        real_cause = st.text_area(
            "Real root cause (your finding from the data):",
            value=st.session_state.get("prefill_real_cause", ""),
            height=90,
            placeholder="What the DuckDB queries revealed...",
        )
        ceo_summary = st.text_area(
            "CEO one-liner (plain English, <280 chars):",
            value=st.session_state.get("prefill_ceo_summary", ""),
            height=90,
            placeholder="Pipeline crashed at 2:47AM due to X; Y transactions delayed; fix deployed at Z; no data lost.",
        )
        cracking_query = st.text_area(
            "The query that cracked the case:",
            value=st.session_state.get("prefill_cracking_query", ""),
            height=60,
            placeholder="Paste the SQL here...",
        )

    # ── Tab auto-fill ─────────────────────────────────────────────────────────
    # Inject JS via iframe: listens for Tab on any empty input/textarea in the
    # parent Streamlit document and fills it with the pre-built answer that
    # matches that field's label text.  Uses React's native-value-setter trick
    # so Streamlit picks up the change properly.
    components.html("""
<script>
(function () {
  var PREFILL = [
    {
      match: "What did BOTH AIs miss",
      value: "TXN012 exists in BOTH TRANSACTIONS_CLEAN and TRANSACTIONS_DIRTY — "
           + "both source lists were loaded into bronze together, creating a "
           + "duplicate row. Neither AI hypothesis mentioned the source data "
           + "overlap; both blamed pipeline code instead."
    },
    {
      match: "Real root cause",
      value: "TXN012 was ingested into bronze_transactions twice — once from the "
           + "clean feed and once from the dirty/retry feed. The pipeline has no "
           + "bronze-level deduplication. Both rows reached the silver INSERT which "
           + "enforces PRIMARY KEY on transaction_id. The second insert raised a "
           + "ConstraintException and crashed the entire pipeline run."
    },
    {
      match: "CEO one-liner",
      value: "Pipeline crashed 2:47AM — duplicate TXN012 across clean+retry source "
           + "feeds caused a PK collision in Silver. 50,471 transactions delayed "
           + "~15 min. Fix: bronze dedup added and deployed. Pipeline restarted "
           + "3:02AM. Zero data lost."
    },
    {
      match: "query that cracked",
      value: "SELECT transaction_id, COUNT(*) AS cnt\\nFROM bronze_transactions\\nGROUP BY transaction_id\\nHAVING cnt > 1"
    }
  ];

  // React-compatible programmatic value setter
  function setReactValue(el, value) {
    var proto = el.tagName === "TEXTAREA"
      ? window.parent.HTMLTextAreaElement.prototype
      : window.parent.HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new window.parent.Event("input",  { bubbles: true }));
    el.dispatchEvent(new window.parent.Event("change", { bubbles: true }));
  }

  function attachTabListeners() {
    var doc = window.parent.document;

    // Streamlit wraps each widget in a div containing a <label> and input/textarea
    var labels = doc.querySelectorAll("label");
    labels.forEach(function (label) {
      var labelText = label.textContent || "";
      PREFILL.forEach(function (item) {
        if (labelText.indexOf(item.match) === -1) return;

        // Walk up to find the nearest container, then down to the field
        var container = label.parentElement;
        while (container && !container.querySelector("input, textarea")) {
          container = container.parentElement;
          if (!container || container === doc.body) return;
        }
        var field = container && container.querySelector("input, textarea");
        if (!field || field._tabFillAttached) return;

        field._tabFillAttached = true;
        field.addEventListener("keydown", function (e) {
          if (e.key === "Tab" && !this.value.trim()) {
            // Fill the field but let Tab continue to move focus naturally
            setReactValue(this, item.value);
          }
        });
      });
    });
  }

  // First attach after initial render
  setTimeout(attachTabListeners, 800);

  // Re-attach whenever Streamlit re-renders (it replaces DOM nodes)
  var observer = new window.parent.MutationObserver(function () {
    setTimeout(attachTabListeners, 400);
  });
  observer.observe(window.parent.document.body, { childList: true, subtree: true });
})();
</script>
""", height=0)

    if st.button("🏆 Close Incident & Generate Report", type="primary", use_container_width=True):
        if not ceo_summary.strip():
            st.warning("Write the CEO one-liner before closing.")
        else:
            el = elapsed()
            m, s = divmod(el, 60)
            st.session_state.verdict = {
                "severity":       final_severity,
                "correct_hyp":    correct_hyp,
                "real_cause":     real_cause,
                "ceo_summary":    ceo_summary,
                "cracking_query": cracking_query,
                "what_ai_missed": what_ai_missed,
                "time_taken":     f"{m}m {s}s",
                "queries_run":    len(st.session_state.query_history),
                "hints_used":     st.session_state.hint_level,
            }
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# VERDICT SCREEN
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.verdict:
    v  = st.session_state.verdict
    r1 = st.session_state.r1_result or {}
    r2 = st.session_state.r2_result or {}

    st.divider()
    st.markdown('<div class="verdict">', unsafe_allow_html=True)
    st.markdown("## 📊 Incident Closed")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Final Severity",  v["severity"])
    m2.metric("Time Taken",      v["time_taken"])
    m3.metric("Queries Run",     v["queries_run"])
    m4.metric("Hints Used",      v["hints_used"])

    st.markdown(f'<div class="ceo" style="margin:16px 0">📧 <b>CEO Message:</b><br><br>"{v["ceo_summary"]}"</div>',
                unsafe_allow_html=True)

    col_a, col_b = st.columns(2, gap="medium")
    with col_a:
        if v.get("real_cause"):
            st.markdown(f'<div class="hyp"><b>🔎 Real Root Cause:</b><br>{v["real_cause"]}</div>',
                        unsafe_allow_html=True)
        if v.get("cracking_query"):
            st.markdown("**🔑 The query that cracked it:**")
            st.code(v["cracking_query"], language="sql")

    with col_b:
        if v.get("what_ai_missed"):
            st.markdown(f'<div class="hyp"><b>🤖 What AI Got Wrong:</b><br>'
                        f'Both hypotheses missed: <i>{v["what_ai_missed"]}</i></div>',
                        unsafe_allow_html=True)
        st.markdown(f'<div class="hyp">'
                    f'<b>AI Hypotheses:</b><br>'
                    f'H1 (Nova Pro): {r1.get("root_cause","—")}<br><br>'
                    f'H2 (Nova Lite): {r2.get("alternative_hypothesis","—")}'
                    f'</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

    # ── Export report ─────────────────────────────────────────────────────────
    report_md = f"""# Incident Report — INC-20240122-0001

**Declared:** 2024-01-22 02:47:33 UTC
**Severity:** {v["severity"]}
**Resolved in:** {v["time_taken"]}
**Queries run:** {v["queries_run"]}
**Hints used:** {v["hints_used"]}

## CEO Summary
> {v["ceo_summary"]}

## Stack Trace
```
{STACK_TRACE}
```

## AI Hypotheses

**H1 — Nova Pro ({r1.get("confidence_pct","?")}% confidence):**
{r1.get("root_cause","—")}

**H2 — Nova Lite ({r2.get("confidence_pct","?")}% confidence):**
{r2.get("alternative_hypothesis","—")}

## Real Root Cause
{v.get("real_cause","Not documented")}

## What Both AIs Missed
{v.get("what_ai_missed","Not documented")}

## The Query That Cracked It
```sql
{v.get("cracking_query","Not documented")}
```

## Query History
{chr(10).join("```sql" + chr(10) + q["sql"] + chr(10) + "```" for q in st.session_state.query_history)}

---
*Generated by Sigma DataTech Incident Commander — Day 9*
"""
    st.download_button(
        "📥 Download Incident Report (.md)",
        report_md,
        file_name="INC-20240122-0001.md",
        mime="text/markdown",
        use_container_width=True,
    )
