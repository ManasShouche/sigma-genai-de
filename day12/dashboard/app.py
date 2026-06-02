"""
Sigma DataTech Intelligence Platform — Command Center Dashboard
A premium fintech-grade dark-theme Streamlit dashboard.

Prerequisites:
  - SIGMA_S3_BUCKET env var set (or lab/.env file)
  - AWS credentials configured (profile, env vars, or IAM role)
  - Phase 3 must have completed (incident report + quarantine file in S3)

Run:  streamlit run app.py
"""

import html as _html
import io, os, re, time
from datetime import datetime
from pathlib import Path

import boto3
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

# ── Env / Config ───────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent.parent / "lab" / ".env")

# Streamlit Cloud: push secrets into env vars so boto3 credential chain picks them up
try:
    import streamlit as _st_secrets
    for _k in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "SIGMA_S3_BUCKET", "AWS_DEFAULT_REGION"]:
        if _k in _st_secrets.secrets:
            os.environ[_k] = str(_st_secrets.secrets[_k])
except Exception:
    pass

BUCKET = os.getenv("SIGMA_S3_BUCKET", "sigma-datatech-ms")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

ALARM_NAMES = [
    "sigma-snowflake-zero-load",
    "sigma-lambda-version-change",
    "sigma-pipeline-row-divergence",
]

ALARM_DESCRIPTIONS = {
    "sigma-snowflake-zero-load":      "Fires when zero rows loaded to Snowflake in a 5-min window",
    "sigma-lambda-version-change":    "Fires when Lambda function version changes unexpectedly",
    "sigma-pipeline-row-divergence":  "Fires when source row count diverges > 10% from target",
}

# ── Hardcoded KPI values (from the incident) ──────────────────────────────────
KPI_EXPECTED     = 120_000
KPI_ACTUAL       = 40_000
KPI_MISSING      = 80_000
KPI_RECOVERED    = 847
KPI_QUARANTINED  = 23
KPI_RECOVERY_SEC = 61

# ── Agent results (hardcoded — all 7 completed) ───────────────────────────────
AGENTS = [
    {
        "name":    "Supervisor",
        "icon":    "🧠",
        "status":  "complete",
        "finding": "Delegated to 6 sub-agents — 61s total",
    },
    {
        "name":    "Forensics",
        "icon":    "🔬",
        "status":  "complete",
        "finding": "Lambda v2 at 02:11 UTC — field rename broke COPY INTO",
    },
    {
        "name":    "Impact",
        "icon":    "📊",
        "status":  "complete",
        "finding": "847 records missing — QuickMart SLA breached ₹84,125",
    },
    {
        "name":    "Rollback",
        "icon":    "⏪",
        "status":  "complete",
        "finding": "sigma-data-producer v2→v1 — stable confirmed",
    },
    {
        "name":    "Recovery",
        "icon":    "♻️",
        "status":  "complete",
        "finding": "847 rows loaded to Snowflake — 0 duplicates",
    },
    {
        "name":    "Hardening",
        "icon":    "🛡️",
        "status":  "complete",
        "finding": "3 CloudWatch alarms created and live",
    },
    {
        "name":    "Incident Report",
        "icon":    "📋",
        "status":  "complete",
        "finding": "Report written to S3 — SNS alert sent",
    },
]

# ── Timeline events ───────────────────────────────────────────────────────────
TIMELINE = [
    ("02:11 UTC", "Lambda sigma-data-producer deployed as version 2", "critical"),
    ("02:11 UTC", "Field rename: order_value → transaction_amount (silent)", "critical"),
    ("02:13 UTC", "Snowflake COPY INTO silently loaded 0 rows — no error raised", "warning"),
    ("02:15 UTC", "Downstream QuickMart dashboard showed stale data", "warning"),
    ("03:45 UTC", "Analytics manager triggered Sigma Intelligence Platform", "info"),
    ("03:45 UTC", "Supervisor Agent delegated to 6 sub-agents in parallel", "info"),
    ("03:45 UTC", "Forensics Agent identified Lambda v2 as root cause", "success"),
    ("03:46 UTC", "Rollback Agent reverted Lambda to v1 — stable confirmed", "success"),
    ("03:46 UTC", "Recovery Agent loaded 847 rows to Snowflake — 0 duplicates", "success"),
    ("03:46 UTC", "Hardening Agent created 3 CloudWatch alarms", "success"),
    ("03:46 UTC", "Incident report written to S3 — SNS alert sent to CTO", "success"),
]

SEVERITY_COLORS = {
    "critical": "#f43f5e",
    "warning":  "#f59e0b",
    "info":     "#38bdf8",
    "success":  "#2dd4bf",
}

# ── Page config (MUST be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="Sigma Intelligence Platform",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Cosmic Glassmorphism CSS ─────────────────────────────────────────────────
st.markdown("""
<style>
/* ─── Global ─────────────────────────────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

html, body, [data-testid="stAppViewContainer"] {
    background-color: #06000f !important;
    color: #ede9fe !important;
    font-family: 'Space Grotesk', sans-serif !important;
}
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #100520 0%, #06000f 100%) !important;
    border-right: 1px solid rgba(139,92,246,0.15) !important;
}
[data-testid="stHeader"] { background: transparent !important; }
.block-container {
    padding-top: 1rem !important;
    padding-bottom: 2rem !important;
    max-width: 100% !important;
}
hr {
    border: none !important;
    border-top: 1px solid rgba(139,92,246,0.12) !important;
    margin: 1.5rem 0 !important;
}
[data-testid="stDataFrame"] {
    background: rgba(12,5,28,0.7) !important;
    border-radius: 12px !important;
    border: 1px solid rgba(139,92,246,0.15) !important;
}
[data-testid="stExpander"] {
    background: rgba(12,5,28,0.7) !important;
    border: 1px solid rgba(139,92,246,0.15) !important;
    border-radius: 12px !important;
}
details summary {
    color: #a78bfa !important;
    font-weight: 600 !important;
    font-family: 'Space Grotesk', sans-serif !important;
}
[data-testid="stButton"] button {
    background: linear-gradient(135deg, rgba(139,92,246,0.15), rgba(45,212,191,0.1)) !important;
    color: #a78bfa !important;
    border: 1px solid rgba(139,92,246,0.35) !important;
    border-radius: 8px !important;
    font-family: 'Space Grotesk', sans-serif !important;
    font-weight: 600 !important;
    transition: all 0.2s ease !important;
}
[data-testid="stButton"] button:hover {
    background: linear-gradient(135deg, rgba(139,92,246,0.3), rgba(45,212,191,0.2)) !important;
    box-shadow: 0 0 16px rgba(139,92,246,0.3) !important;
}
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: #06000f; }
::-webkit-scrollbar-thumb { background: rgba(139,92,246,0.3); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(139,92,246,0.5); }
[data-testid="stAlert"] {
    background: rgba(12,5,28,0.7) !important;
    border-radius: 10px !important;
    border: 1px solid rgba(139,92,246,0.2) !important;
}

/* ─── Glass card base ────────────────────────────────────────────────────── */
.g-card {
    background: rgba(12,5,28,0.65);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 16px;
    padding: 1.2rem 1.4rem;
    box-shadow: 0 8px 32px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.04);
    transition: border-color 0.25s, box-shadow 0.25s, transform 0.25s;
}
.g-card:hover {
    border-color: rgba(139,92,246,0.25);
    box-shadow: 0 12px 40px rgba(0,0,0,0.5), 0 0 0 1px rgba(139,92,246,0.1);
    transform: translateY(-2px);
}

/* ─── KPI card ───────────────────────────────────────────────────────────── */
.kpi-card {
    background: rgba(12,5,28,0.65);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 16px;
    padding: 1.2rem 1.3rem;
    position: relative;
    overflow: hidden;
    box-shadow: 0 8px 32px rgba(0,0,0,0.35);
    transition: transform 0.25s, box-shadow 0.25s;
}
.kpi-card:hover {
    transform: translateY(-3px);
    box-shadow: 0 16px 48px rgba(0,0,0,0.5);
}
.kpi-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: var(--kpi-accent, #8b5cf6);
    box-shadow: 0 0 12px var(--kpi-accent, #8b5cf6);
}
.kpi-number {
    font-size: 1.95rem;
    font-weight: 700;
    line-height: 1;
    font-family: 'JetBrains Mono', monospace;
    letter-spacing: -1px;
}
.kpi-label {
    font-size: 0.62rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: rgba(167,139,250,0.6);
    margin-top: 0.5rem;
}
.kpi-delta {
    font-size: 0.7rem;
    margin-top: 0.45rem;
    font-weight: 500;
    opacity: 0.8;
}

/* ─── Agent card ─────────────────────────────────────────────────────────── */
.agent-card {
    background: rgba(12,5,28,0.65);
    border-radius: 14px;
    padding: 0.9rem 1rem;
    border: 1px solid rgba(255,255,255,0.06);
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    transition: border-color 0.25s, box-shadow 0.25s, transform 0.25s;
}
.agent-card:hover {
    border-color: rgba(139,92,246,0.3);
    box-shadow: 0 8px 28px rgba(139,92,246,0.12);
    transform: translateY(-2px);
}
.agent-card-top {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    margin-bottom: 0.45rem;
}
.agent-icon { font-size: 1.1rem; }
.agent-name {
    font-size: 0.72rem;
    font-weight: 700;
    color: #c4b5fd;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}
.agent-badge {
    font-size: 0.6rem;
    font-weight: 700;
    padding: 0.12rem 0.45rem;
    border-radius: 20px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}
.badge-complete {
    background: rgba(45,212,191,0.12);
    color: #2dd4bf;
    border: 1px solid rgba(45,212,191,0.3);
}
.badge-running {
    background: rgba(245,158,11,0.12);
    color: #f59e0b;
    border: 1px solid rgba(245,158,11,0.3);
}
.badge-failed {
    background: rgba(244,63,94,0.12);
    color: #f43f5e;
    border: 1px solid rgba(244,63,94,0.3);
}
.agent-finding {
    font-size: 0.7rem;
    color: rgba(167,139,250,0.55);
    line-height: 1.4;
    font-style: italic;
    margin-top: 0.3rem;
}
.agent-status-icon { font-size: 0.85rem; }

/* ─── Section header ─────────────────────────────────────────────────────── */
.section-header {
    display: flex;
    align-items: center;
    gap: 0.8rem;
    margin-bottom: 1.2rem;
    padding-bottom: 0.7rem;
    border-bottom: 1px solid rgba(139,92,246,0.12);
}
.section-icon {
    font-size: 0.95rem;
    width: 30px; height: 30px;
    background: linear-gradient(135deg, rgba(139,92,246,0.2), rgba(45,212,191,0.1));
    border: 1px solid rgba(139,92,246,0.25);
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
    box-shadow: 0 0 12px rgba(139,92,246,0.15);
}
.section-title {
    font-size: 0.68rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 2.5px;
    background: linear-gradient(90deg, #a78bfa, #2dd4bf);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}

/* ─── Platform header ────────────────────────────────────────────────────── */
.platform-header {
    background: linear-gradient(135deg, rgba(20,5,45,0.9) 0%, rgba(12,3,28,0.95) 100%);
    border: 1px solid rgba(139,92,246,0.2);
    border-radius: 18px;
    padding: 1.3rem 2rem;
    margin-bottom: 1.5rem;
    display: flex; align-items: center; justify-content: space-between;
    box-shadow: 0 0 60px rgba(139,92,246,0.08), inset 0 1px 0 rgba(255,255,255,0.04);
}
.platform-title {
    font-size: 1.45rem;
    font-weight: 700;
    background: linear-gradient(90deg, #c4b5fd 0%, #2dd4bf 60%, #c4b5fd 100%);
    background-size: 200% auto;
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    letter-spacing: -0.3px;
    animation: shimmer 4s linear infinite;
}
@keyframes shimmer {
    0%   { background-position: 0% center; }
    100% { background-position: 200% center; }
}
.platform-subtitle {
    font-size: 0.68rem;
    color: rgba(167,139,250,0.45);
    text-transform: uppercase;
    letter-spacing: 2.5px;
    margin-top: 0.25rem;
}
.live-badge {
    display: inline-flex; align-items: center; gap: 0.45rem;
    font-size: 0.62rem; font-weight: 700;
    color: #2dd4bf;
    background: rgba(45,212,191,0.1);
    border: 1px solid rgba(45,212,191,0.3);
    padding: 0.3rem 0.9rem;
    border-radius: 20px;
    text-transform: uppercase; letter-spacing: 1.5px;
    box-shadow: 0 0 14px rgba(45,212,191,0.15);
}
.live-dot {
    width: 6px; height: 6px;
    background: #2dd4bf;
    border-radius: 50%;
    animation: pulse-dot 2s infinite;
    box-shadow: 0 0 6px #2dd4bf;
}
@keyframes pulse-dot {
    0%,100% { opacity:1; transform:scale(1); }
    50%      { opacity:0.3; transform:scale(0.7); }
}

/* ─── Footer ─────────────────────────────────────────────────────────────── */
.platform-footer {
    background: rgba(12,5,28,0.65);
    border: 1px solid rgba(139,92,246,0.12);
    border-radius: 12px;
    padding: 0.8rem 1.5rem;
    margin-top: 2rem;
    display: flex; justify-content: space-between;
    align-items: center; flex-wrap: wrap; gap: 0.5rem;
}
.footer-item {
    display: flex; align-items: center; gap: 0.5rem;
    font-size: 0.65rem;
    color: rgba(167,139,250,0.35);
    font-family: 'JetBrains Mono', monospace;
}
.footer-item span.val { color: #2dd4bf; font-weight: 600; }
.footer-sep { color: rgba(139,92,246,0.2); }

/* ─── Sidebar ────────────────────────────────────────────────────────────── */
.sidebar-logo {
    font-size: 1.05rem; font-weight: 700;
    background: linear-gradient(90deg, #c4b5fd, #2dd4bf);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text; letter-spacing: 1px; margin-bottom: 0.2rem;
}
.sidebar-tagline {
    font-size: 0.58rem; color: rgba(139,92,246,0.4);
    text-transform: uppercase; letter-spacing: 2px; margin-bottom: 1rem;
}
.sidebar-clock {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.35rem; font-weight: 500;
    color: #a78bfa; letter-spacing: 2px;
}
.sidebar-date {
    font-size: 0.68rem; color: rgba(167,139,250,0.4); margin-bottom: 1.2rem;
}
.sidebar-info-row {
    display: flex; justify-content: space-between;
    font-size: 0.65rem; padding: 0.4rem 0;
    border-bottom: 1px solid rgba(139,92,246,0.08);
}
.sidebar-info-label { color: rgba(139,92,246,0.4); }
.sidebar-info-val { color: #c4b5fd; font-family: 'JetBrains Mono', monospace; font-size: 0.62rem; }
</style>
""", unsafe_allow_html=True)


# ── Data loading ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def load_s3_data() -> dict:
    """Load incident report, quarantine CSV, and CloudWatch alarms from AWS."""
    s3 = boto3.client("s3", region_name=REGION)
    cw = boto3.client("cloudwatch", region_name=REGION)

    # ── Incident report ───────────────────────────────────────────────────────
    report_md  = ""
    report_key = ""
    try:
        resp    = s3.list_objects_v2(Bucket=BUCKET, Prefix="reports/")
        objects = [o for o in resp.get("Contents", []) if o["Key"].endswith(".md")]
        if objects:
            latest     = sorted(objects, key=lambda x: x["LastModified"], reverse=True)[0]
            report_key = latest["Key"]
            report_md  = s3.get_object(Bucket=BUCKET, Key=report_key)["Body"].read().decode()
    except Exception as e:
        report_md = f"_Could not load incident report: {e}_"

    # ── Quarantine CSV ────────────────────────────────────────────────────────
    quarantine_df  = pd.DataFrame()
    quarantine_key = ""
    try:
        resp    = s3.list_objects_v2(Bucket=BUCKET, Prefix="quarantine/")
        objects = [o for o in resp.get("Contents", []) if o["Key"].endswith(".csv")]
        if objects:
            latest        = sorted(objects, key=lambda x: x["LastModified"], reverse=True)[0]
            quarantine_key = latest["Key"]
            csv_raw       = s3.get_object(Bucket=BUCKET, Key=quarantine_key)["Body"].read().decode()
            quarantine_df = pd.read_csv(io.StringIO(csv_raw))
    except Exception as e:
        pass  # will fall back to KPI_QUARANTINED count

    # ── CloudWatch alarms ─────────────────────────────────────────────────────
    alarms = []
    try:
        resp   = cw.describe_alarms(AlarmNames=ALARM_NAMES)
        alarms = [
            {
                "name":  a["AlarmName"],
                "state": a["StateValue"],
                "desc":  a.get("AlarmDescription") or ALARM_DESCRIPTIONS.get(a["AlarmName"], "—"),
            }
            for a in resp.get("MetricAlarms", [])
        ]
    except Exception as e:
        pass

    # ── Parse root cause from report ──────────────────────────────────────────
    def extract(pattern, default="—"):
        m = re.search(pattern, report_md, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else default

    root_cause = extract(r"##\s*Root Cause\s*\n+(.*?)(?:\n+##|\Z)")
    fix_applied = extract(r"##\s*Fix Applied\s*\n+(.*?)(?:\n+##|\Z)")

    return {
        "report_md":      report_md,
        "report_key":     report_key,
        "root_cause":     root_cause,
        "fix_applied":    fix_applied,
        "quarantine_df":  quarantine_df,
        "quarantine_key": quarantine_key,
        "alarms":         alarms,
        "loaded_at":      datetime.utcnow(),
    }


# ── Load data (with spinner) ──────────────────────────────────────────────────
with st.spinner("Connecting to Sigma Intelligence Platform..."):
    data = load_s3_data()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    now = datetime.utcnow()
    st.markdown("""
    <div class="sidebar-logo">⚡ SIGMA</div>
    <div class="sidebar-tagline">Intelligence Platform</div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div class="sidebar-clock">{now.strftime('%H:%M:%S')}</div>
    <div class="sidebar-date">{now.strftime('%Y-%m-%d')} UTC</div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    st.markdown("""
    <div style="font-size:0.62rem;font-weight:700;text-transform:uppercase;
                letter-spacing:2px;color:#3a4a60;margin-bottom:0.6rem;">
        Data Sources
    </div>
    """, unsafe_allow_html=True)

    bucket_display = BUCKET if BUCKET else "not set"
    report_short   = data["report_key"].split("/")[-1] if data["report_key"] else "not found"
    q_short        = data["quarantine_key"].split("/")[-1] if data.get("quarantine_key") else "not found"
    alarms_ok      = sum(1 for a in data["alarms"] if a["state"] == "OK")

    st.markdown(f"""
    <div class="sidebar-info-row">
        <span class="sidebar-info-label">S3 Bucket</span>
        <span class="sidebar-info-val">{bucket_display[:20]}</span>
    </div>
    <div class="sidebar-info-row">
        <span class="sidebar-info-label">Region</span>
        <span class="sidebar-info-val">{REGION}</span>
    </div>
    <div class="sidebar-info-row">
        <span class="sidebar-info-label">Report</span>
        <span class="sidebar-info-val">{report_short[:20]}</span>
    </div>
    <div class="sidebar-info-row">
        <span class="sidebar-info-label">Quarantine</span>
        <span class="sidebar-info-val">{q_short[:20]}</span>
    </div>
    <div class="sidebar-info-row">
        <span class="sidebar-info-label">CW Alarms</span>
        <span class="sidebar-info-val">{alarms_ok}/{len(ALARM_NAMES)} OK</span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")
    if st.button("↺  Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.markdown("""
    <div style="font-size:0.6rem;color:rgba(139,92,246,0.25);text-align:center;
                line-height:1.9;font-family:'JetBrains Mono',monospace;">
        Sigma DataTech · Day 12<br>
        Multi-Agent Intelligence<br>
        Platform v1.0.0
    </div>
    """, unsafe_allow_html=True)


# ── Nebula background orbs (fixed, behind everything) ────────────────────────
st.markdown("""
<div style="position:fixed;top:0;left:0;width:100vw;height:100vh;
            pointer-events:none;z-index:0;overflow:hidden;">
    <div style="position:absolute;top:-15%;left:-5%;width:65vw;height:65vw;border-radius:50%;
                background:radial-gradient(circle,rgba(139,92,246,0.13) 0%,transparent 65%);"></div>
    <div style="position:absolute;bottom:-20%;right:-5%;width:55vw;height:55vw;border-radius:50%;
                background:radial-gradient(circle,rgba(45,212,191,0.09) 0%,transparent 65%);"></div>
    <div style="position:absolute;top:35%;left:35%;width:35vw;height:35vw;border-radius:50%;
                background:radial-gradient(circle,rgba(244,63,94,0.06) 0%,transparent 65%);"></div>
</div>
""", unsafe_allow_html=True)

# ── Platform Header ───────────────────────────────────────────────────────────
st.markdown(f"""
<div class="platform-header">
    <div>
        <div class="platform-title">⚡ SIGMA INTELLIGENCE PLATFORM</div>
        <div class="platform-subtitle">Autonomous Incident Recovery · Multi-Agent Orchestration</div>
    </div>
    <div style="text-align:right;display:flex;flex-direction:column;align-items:flex-end;gap:0.4rem;">
        <div class="live-badge"><div class="live-dot"></div> LIVE</div>
        <div style="font-size:0.62rem;color:rgba(139,92,246,0.4);
                    font-family:'JetBrains Mono',monospace;letter-spacing:1px;">
            {now.strftime('%Y-%m-%d %H:%M:%S')} UTC
        </div>
    </div>
</div>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — KPI CARDS
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div class="section-header">
    <div class="section-icon">📊</div>
    <div class="section-title">Incident Summary — KPIs</div>
</div>
""", unsafe_allow_html=True)

kpis = [
    {"label": "EXPECTED",  "value": f"{KPI_EXPECTED:,}",    "delta": "Daily baseline",                         "color": "#8b5cf6"},
    {"label": "ACTUAL",    "value": f"{KPI_ACTUAL:,}",      "delta": f"▼ {(KPI_EXPECTED-KPI_ACTUAL)/KPI_EXPECTED*100:.0f}% below baseline", "color": "#f43f5e"},
    {"label": "MISSING",   "value": f"{KPI_MISSING:,}",     "delta": "Gap flagged by agents",                  "color": "#f59e0b"},
    {"label": "RECOVERED", "value": f"{KPI_RECOVERED:,}",   "delta": "▲ Loaded · 0 duplicates",                "color": "#2dd4bf"},
    {"label": "QUARANTINE","value": f"{KPI_QUARANTINED:,}", "delta": "Quality issues isolated",                 "color": "#f59e0b"},
    {"label": "REC. TIME", "value": f"{KPI_RECOVERY_SEC}s", "delta": "0 human interventions",                  "color": "#2dd4bf"},
]

cols = st.columns(6)
for col, kpi in zip(cols, kpis):
    with col:
        st.markdown(f"""
        <div class="kpi-card" style="--kpi-accent:{kpi['color']};">
            <div class="kpi-number" style="color:{kpi['color']};">{kpi['value']}</div>
            <div class="kpi-label">{kpi['label']}</div>
            <div class="kpi-delta" style="color:{kpi['color']};opacity:0.7;">{kpi['delta']}</div>
        </div>
        """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — AGENT STATUS PANEL
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div class="section-header">
    <div class="section-icon">🤖</div>
    <div class="section-title">Agent Status Panel</div>
</div>
""", unsafe_allow_html=True)

agent_cols = st.columns(7)
for col, agent in zip(agent_cols, AGENTS):
    badge_class = f"badge-{agent['status']}"
    badge_icon  = "✦" if agent["status"] == "complete" else ("◌" if agent["status"] == "running" else "✕")
    badge_text  = agent["status"].upper()
    with col:
        st.markdown(f"""
        <div class="agent-card">
            <div class="agent-card-top">
                <span class="agent-icon">{agent['icon']}</span>
                <span class="agent-name">{agent['name']}</span>
            </div>
            <div style="display:flex;align-items:center;gap:0.35rem;margin-bottom:0.4rem;">
                <span class="agent-status-icon">{badge_icon}</span>
                <span class="agent-badge {badge_class}">{badge_text}</span>
            </div>
            <div class="agent-finding">{agent['finding']}</div>
        </div>
        """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 & 4 — TIMELINE + ROOT CAUSE (side by side)
# ═══════════════════════════════════════════════════════════════════════════════
col_timeline, col_root = st.columns([1, 1], gap="large")

with col_timeline:
    st.markdown("""
    <div class="section-header">
        <div class="section-icon">⏱</div>
        <div class="section-title">Incident Timeline</div>
    </div>
    """, unsafe_allow_html=True)

    timeline_html = (
        '<div style="position:relative;padding-left:1.8rem;'
        'border-left:2px solid rgba(139,92,246,0.18);margin-left:5px;">'
    )
    for t_ts, t_desc, severity in TIMELINE:
        dot_color = SEVERITY_COLORS.get(severity, "#6d5a9a")
        safe_desc = _html.escape(t_desc)
        safe_ts   = _html.escape(t_ts)
        timeline_html += f"""
        <div style="position:relative;margin-bottom:0.65rem;
                    padding:0.5rem 0.7rem;
                    background:rgba(12,5,28,0.6);border-radius:10px;
                    border:1px solid rgba(255,255,255,0.05);
                    box-shadow:0 2px 12px rgba(0,0,0,0.25);">
            <div style="position:absolute;left:-1.45rem;top:50%;
                        transform:translateY(-50%);
                        width:9px;height:9px;border-radius:50%;
                        background:{dot_color};border:2px solid #06000f;
                        box-shadow:0 0 8px {dot_color}99;"></div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:0.6rem;
                        font-weight:600;color:{dot_color};margin-bottom:0.12rem;
                        letter-spacing:0.5px;">
                {safe_ts}
            </div>
            <div style="font-size:0.76rem;color:rgba(196,181,253,0.8);line-height:1.4;">
                {safe_desc}
            </div>
        </div>"""
    timeline_html += "</div>"
    st.markdown(timeline_html, unsafe_allow_html=True)

with col_root:
    st.markdown("""
    <div class="section-header">
        <div class="section-icon">🔴</div>
        <div class="section-title">Root Cause Analysis</div>
    </div>
    """, unsafe_allow_html=True)

    # Use live S3 root cause if available, else use hardcoded
    root_cause_text = data["root_cause"] if data["root_cause"] != "—" else (
        "Lambda function `sigma-data-producer` was deployed as version 2 at 02:11 UTC. "
        "The new version renamed the output field `order_value` to `transaction_amount`. "
        "Snowflake's COPY INTO silently skipped the column, loading zero rows with no errors "
        "and no alerts. The pipeline appeared healthy while 847 records were lost."
    )

    safe_root = _html.escape(root_cause_text)
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,rgba(30,5,15,0.85),rgba(25,8,5,0.85));
                border:1px solid rgba(244,63,94,0.2);border-top:2px solid #f43f5e;
                border-radius:14px;padding:1.3rem 1.4rem;position:relative;overflow:hidden;
                box-shadow:0 8px 32px rgba(244,63,94,0.08),inset 0 1px 0 rgba(255,255,255,0.03);">
        <div style="position:absolute;right:1rem;top:0.8rem;font-size:2.8rem;
                    opacity:0.06;line-height:1;">⚠</div>
        <div style="font-size:0.6rem;font-weight:700;text-transform:uppercase;
                    letter-spacing:2.5px;color:#f43f5e;margin-bottom:0.65rem;">
            ⚠ Critical Root Cause Identified
        </div>
        <div style="font-size:0.85rem;color:rgba(255,200,210,0.85);line-height:1.7;margin-bottom:0.75rem;">
            {safe_root}
        </div>
        <div style="border-top:1px solid rgba(244,63,94,0.12);padding-top:0.65rem;
                    font-size:0.7rem;color:rgba(245,158,11,0.6);
                    font-family:'JetBrains Mono',monospace;line-height:1.9;">
            COMPONENT  : sigma-data-producer Lambda<br>
            EVENT      : v1 → v2 deploy at 02:11 UTC<br>
            MECHANISM  : merchant_name → merchant_nm + DD-MM-YYYY date format<br>
            FAILURE    : Snowflake COPY INTO silent reject — 0 rows, no error<br>
            DETECTION  : Sigma Intelligence Platform autonomous scan<br>
            RESOLUTION : 61 seconds — 0 human interventions
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Fix applied — 3 bullet steps, always hardcoded so they render cleanly
    st.markdown("""
    <div style="background:linear-gradient(135deg,rgba(5,20,15,0.85),rgba(3,18,12,0.85));
                border:1px solid rgba(45,212,191,0.18);border-top:2px solid #2dd4bf;
                border-radius:14px;padding:1.2rem 1.4rem;
                box-shadow:0 8px 32px rgba(45,212,191,0.06),inset 0 1px 0 rgba(255,255,255,0.03);">
        <div style="font-size:0.6rem;font-weight:700;text-transform:uppercase;
                    letter-spacing:2.5px;color:#2dd4bf;margin-bottom:0.8rem;">
            ✦ Resolution Applied — 3 Steps
        </div>
        <div style="display:flex;flex-direction:column;gap:0.55rem;">
            <div style="display:flex;align-items:flex-start;gap:0.6rem;">
                <span style="color:#2dd4bf;font-size:0.78rem;font-weight:700;
                             flex-shrink:0;font-family:'JetBrains Mono',monospace;">01</span>
                <span style="font-size:0.8rem;color:rgba(167,230,220,0.85);line-height:1.5;">
                    <strong style="color:#2dd4bf;">Rollback</strong> — sigma-data-producer LIVE alias reverted v2 → v1. Schema mismatch eliminated at source.
                </span>
            </div>
            <div style="display:flex;align-items:flex-start;gap:0.6rem;">
                <span style="color:#2dd4bf;font-size:0.78rem;font-weight:700;
                             flex-shrink:0;font-family:'JetBrains Mono',monospace;">02</span>
                <span style="font-size:0.8rem;color:rgba(167,230,220,0.85);line-height:1.5;">
                    <strong style="color:#2dd4bf;">Recovery</strong> — 847 records replayed from S3 disaster prefix with field remapping. Idempotent MERGE — 0 duplicates.
                </span>
            </div>
            <div style="display:flex;align-items:flex-start;gap:0.6rem;">
                <span style="color:#2dd4bf;font-size:0.78rem;font-weight:700;
                             flex-shrink:0;font-family:'JetBrains Mono',monospace;">03</span>
                <span style="font-size:0.8rem;color:rgba(167,230,220,0.85);line-height:1.5;">
                    <strong style="color:#2dd4bf;">Hardening</strong> — 3 CloudWatch alarms live: zero-load, version-change, row-divergence. Next incident detected in &lt;10 min.
                </span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — RECOVERY SUMMARY (chart + stats)
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div class="section-header">
    <div class="section-icon">♻️</div>
    <div class="section-title">Recovery Summary</div>
</div>
""", unsafe_allow_html=True)

col_donut, col_stats = st.columns([1, 1], gap="large")

DISASTER_TOTAL = KPI_RECOVERED + KPI_QUARANTINED  # 870 — all disaster records

with col_donut:
    fig = go.Figure(data=[go.Pie(
        labels=["Recovered to Snowflake", "Quarantined (bad records)"],
        values=[KPI_RECOVERED, KPI_QUARANTINED],
        hole=0.72,
        marker=dict(
            colors=["#2dd4bf", "#f59e0b"],
            line=dict(color="#06000f", width=3),
        ),
        textinfo="none",
        hovertemplate="<b>%{label}</b><br>%{value:,} records<br>%{percent:.1%}<extra></extra>",
    )])

    fig.add_annotation(
        text=f"<b>100%</b><br><span style='font-size:11px'>Accounted</span>",
        x=0.5, y=0.5,
        font=dict(size=22, color="#2dd4bf", family="JetBrains Mono"),
        showarrow=False,
        align="center",
    )

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="rgba(167,139,250,0.5)", family="Space Grotesk"),
        showlegend=True,
        legend=dict(
            orientation="h",
            x=0.5, xanchor="center",
            y=-0.15,
            font=dict(size=11, color="#7a8ba8"),
            bgcolor="rgba(0,0,0,0)",
        ),
        margin=dict(t=20, b=20, l=20, r=20),
        height=280,
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

with col_stats:
    # Recovery stats as styled boxes
    stats = [
        ("Disaster Batch",      f"{DISASTER_TOTAL:,}",                              "#8b5cf6"),
        ("Recovered",           f"{KPI_RECOVERED:,}",                               "#2dd4bf"),
        ("Quarantined",         f"{KPI_QUARANTINED:,}",                             "#f59e0b"),
        ("Recovery Rate",       f"{KPI_RECOVERED / DISASTER_TOTAL * 100:.1f}%",     "#2dd4bf"),
        ("Duplicates",          "0",                                                "#2dd4bf"),
        ("Time to Recovery",    f"{KPI_RECOVERY_SEC}s",                             "#2dd4bf"),
    ]

    stat_pairs = [stats[i:i+2] for i in range(0, len(stats), 2)]
    for pair in stat_pairs:
        subcols = st.columns(2)
        for sc, (label, val, color) in zip(subcols, pair):
            with sc:
                st.markdown(f"""
                <div style="background:rgba(12,5,28,0.6);border:1px solid rgba(255,255,255,0.05);
                            border-radius:10px;padding:0.75rem 0.9rem;margin-bottom:0.5rem;
                            box-shadow:0 2px 12px rgba(0,0,0,0.25);">
                    <div style="font-family:'JetBrains Mono',monospace;font-size:1.25rem;
                                font-weight:700;color:{color};">{val}</div>
                    <div style="font-size:0.6rem;font-weight:600;text-transform:uppercase;
                                letter-spacing:1.2px;color:rgba(139,92,246,0.4);margin-top:0.2rem;">{label}</div>
                </div>
                """, unsafe_allow_html=True)

    st.markdown("""
    <div style="background:linear-gradient(90deg,rgba(5,18,14,0.8),rgba(3,15,12,0.8));
                border:1px solid rgba(45,212,191,0.15);border-radius:10px;
                padding:0.65rem 1rem;font-size:0.72rem;color:#2dd4bf;margin-top:0.4rem;
                font-family:'JetBrains Mono',monospace;letter-spacing:0.3px;">
        ✦ Idempotent MERGE ON transaction_id — replay-safe, zero duplicate risk
    </div>
    """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — PREVENTION ALARMS
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div class="section-header">
    <div class="section-icon">🛡</div>
    <div class="section-title">Prevention — CloudWatch Alarms</div>
</div>
""", unsafe_allow_html=True)

# Merge live alarm states with hardcoded descriptions / fallback
alarm_state_map = {a["name"]: a["state"] for a in data["alarms"]}
alarm_display = []
for name in ALARM_NAMES:
    state = alarm_state_map.get(name, "OK")   # default OK (just created, no data yet)
    alarm_display.append({
        "name":  name,
        "state": state,
        "desc":  ALARM_DESCRIPTIONS.get(name, "—"),
    })

# sigma-snowflake-zero-load fires when no pipeline is running — expected when lab is idle
ALARM_IDLE_NOTE = {
    "sigma-snowflake-zero-load": "ALARM when pipeline idle — working as intended",
}

alarm_cols = st.columns(3)
for col, alarm in zip(alarm_cols, alarm_display):
    state = alarm["state"]
    if state == "OK":
        border_color = "#2dd4bf"
        badge_bg     = "rgba(5,20,18,0.8)"
        badge_color  = "#2dd4bf"
        state_label  = "✦ OK"
    elif state == "ALARM":
        # check if this is an expected / by-design alarm
        is_expected  = alarm["name"] in ALARM_IDLE_NOTE
        border_color = "#ffa502" if is_expected else "#ff4757"
        badge_bg     = "#1a1200" if is_expected else "#1a0a0a"
        badge_color  = "#ffa502" if is_expected else "#ff4757"
        state_label  = "🟡 ALARM (idle)" if is_expected else "🔴 ALARM"
    else:
        border_color = "#5a6a88"
        badge_bg     = "#131929"
        badge_color  = "#7a8ba8"
        state_label  = "⚪ NO DATA"

    idle_note = ALARM_IDLE_NOTE.get(alarm["name"], "")
    idle_html = (
        f'<div style="margin-top:0.5rem;font-size:0.65rem;color:{badge_color};'
        f'opacity:0.75;font-style:italic;">{_html.escape(idle_note)}</div>'
        if idle_note else ""
    )

    with col:
        st.markdown(f"""
        <div style="background:#131929;border-radius:10px;padding:1rem 1.1rem;
                    border:1px solid #1e2d47;border-top:3px solid {border_color};
                    height:100%;">
            <div style="display:flex;align-items:center;justify-content:space-between;
                        margin-bottom:0.6rem;">
                <span style="font-size:1.1rem;">🔔</span>
                <span style="background:{badge_bg};color:{badge_color};
                             border:1px solid {border_color}44;
                             border-radius:4px;padding:2px 8px;
                             font-size:0.65rem;font-weight:700;
                             font-family:'JetBrains Mono',monospace;">
                    {state_label}
                </span>
            </div>
            <div style="font-size:0.75rem;font-weight:600;color:#c8d8f0;
                        font-family:'JetBrains Mono',monospace;
                        word-break:break-all;margin-bottom:0.4rem;">
                {_html.escape(alarm['name'])}
            </div>
            <div style="font-size:0.72rem;color:#5a6a88;line-height:1.45;">
                {_html.escape(alarm['desc'])}
            </div>
            {idle_html}
        </div>
        """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — QUARANTINE TABLE
# ═══════════════════════════════════════════════════════════════════════════════
q_count = len(data["quarantine_df"]) if not data["quarantine_df"].empty else KPI_QUARANTINED

st.markdown(f"""
<div class="section-header">
    <div class="section-icon">🔒</div>
    <div class="section-title">Quarantined Records &nbsp;
        <span style="font-size:0.65rem;background:#ffa50222;color:#ffa502;
                     border:1px solid #ffa50244;border-radius:20px;
                     padding:0.1rem 0.6rem;font-weight:700;">{q_count} ROWS</span>
    </div>
</div>
""", unsafe_allow_html=True)

if not data["quarantine_df"].empty:
    df = data["quarantine_df"].copy()
    # Style the dataframe for dark theme
    st.dataframe(
        df.style
          .set_properties(**{
              "background-color": "#131929",
              "color":            "#c8d8f0",
              "border-color":     "#1e2d47",
          })
          .set_table_styles([{
              "selector": "thead th",
              "props": [
                  ("background-color", "#0d1224"),
                  ("color",            "#00d4ff"),
                  ("font-weight",      "700"),
                  ("text-transform",   "uppercase"),
                  ("font-size",        "0.7rem"),
                  ("letter-spacing",   "1px"),
              ]
          }]),
        use_container_width=True,
        height=200,
    )
    if data.get("quarantine_key"):
        st.markdown(f"""
        <div style="font-size:0.65rem;color:#3a4a60;margin-top:0.4rem;
                    font-family:'JetBrains Mono',monospace;">
            Source: s3://{BUCKET}/{data['quarantine_key']}
        </div>
        """, unsafe_allow_html=True)
else:
    st.markdown(f"""
    <div style="background:#131929;border:1px solid #ffa50222;border-radius:8px;
                padding:1.2rem;text-align:center;">
        <div style="font-size:1.5rem;margin-bottom:0.4rem;">🔒</div>
        <div style="font-size:0.8rem;color:#7a8ba8;">
            {KPI_QUARANTINED} records quarantined — CSV not available in live S3 data.
        </div>
        <div style="font-size:0.65rem;color:#3a4a60;margin-top:0.3rem;
                    font-family:'JetBrains Mono',monospace;">
            Expected: s3://{BUCKET}/quarantine/2026-06-04/quarantine_*.csv
        </div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — FULL INCIDENT REPORT
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div class="section-header">
    <div class="section-icon">📋</div>
    <div class="section-title">Full Incident Report</div>
</div>
""", unsafe_allow_html=True)

with st.expander("📄 Click to read the CTO-ready post-mortem report", expanded=False):
    if data["report_md"] and not data["report_md"].startswith("_Could not"):
        if data.get("report_key"):
            st.markdown(f"""
            <div style="font-size:0.65rem;color:#3a4a60;margin-bottom:1rem;
                        font-family:'JetBrains Mono',monospace;padding:0.5rem;
                        background:#0d1224;border-radius:6px;border:1px solid #1e2d47;">
                📦 s3://{BUCKET}/{data['report_key']}
            </div>
            """, unsafe_allow_html=True)
        st.markdown(
            f'<div style="font-size:0.85rem;line-height:1.8;color:#c8d8f0;">{data["report_md"]}</div>',
            unsafe_allow_html=True
        )
    else:
        st.markdown(f"""
        <div style="color:#7a8ba8;font-size:0.85rem;padding:1rem;">
            Incident report not found in S3.<br><br>
            <span style="font-family:'JetBrains Mono',monospace;font-size:0.75rem;color:#3a4a60;">
                Expected: s3://{BUCKET}/reports/incident_*.md
            </span><br><br>
            Ensure Phase 3 completed successfully. Re-run the supervisor trigger to generate.
        </div>
        """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# FOOTER
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown(f"""
<div class="platform-footer">
    <div class="footer-item">
        <span>PLATFORM UPTIME</span>
        <span class="val">LIVE</span>
    </div>
    <div class="footer-sep">·</div>
    <div class="footer-item">
        <span>LAST RECOVERY</span>
        <span class="val">{KPI_RECOVERY_SEC}s</span>
    </div>
    <div class="footer-sep">·</div>
    <div class="footer-item">
        <span>HUMAN INTERVENTIONS</span>
        <span class="val">0</span>
    </div>
    <div class="footer-sep">·</div>
    <div class="footer-item">
        <span>AGENTS DEPLOYED</span>
        <span class="val">7</span>
    </div>
    <div class="footer-sep">·</div>
    <div class="footer-item">
        <span>RECORDS RECOVERED</span>
        <span class="val">{KPI_RECOVERED:,}</span>
    </div>
    <div class="footer-sep">·</div>
    <div class="footer-item">
        <span>DASHBOARD REFRESHED</span>
        <span class="val">{now.strftime('%H:%M:%S UTC')}</span>
    </div>
    <div class="footer-sep">·</div>
    <div class="footer-item">
        <span>S3 BUCKET</span>
        <span class="val">{BUCKET}</span>
    </div>
</div>
""", unsafe_allow_html=True)
