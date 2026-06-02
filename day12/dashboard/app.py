"""
Sigma DataTech Intelligence Platform — Command Center Dashboard
A premium fintech-grade dark-theme Streamlit dashboard.

Prerequisites:
  - SIGMA_S3_BUCKET env var set (or lab/.env file)
  - AWS credentials configured (profile, env vars, or IAM role)
  - Phase 3 must have completed (incident report + quarantine file in S3)

Run:  streamlit run app.py
"""

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
KPI_QUARANTINED  = 2
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
    "critical": "#ff4757",
    "warning":  "#ffa502",
    "info":     "#00d4ff",
    "success":  "#2ed573",
}

# ── Page config (MUST be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="Sigma Intelligence Platform",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Premium Dark Theme CSS ────────────────────────────────────────────────────
st.markdown("""
<style>
/* ─── Global ─────────────────────────────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, [data-testid="stAppViewContainer"] {
    background-color: #0a0e1a !important;
    color: #e0e6f0 !important;
    font-family: 'Inter', sans-serif !important;
}

[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0d1224 0%, #0a0e1a 100%) !important;
    border-right: 1px solid #1e2d47 !important;
}

[data-testid="stHeader"] {
    background: transparent !important;
}

/* ─── Remove Streamlit default padding top ───────────────────────────────── */
.block-container {
    padding-top: 1rem !important;
    padding-bottom: 2rem !important;
    max-width: 100% !important;
}

/* ─── Section dividers ───────────────────────────────────────────────────── */
hr {
    border: none !important;
    border-top: 1px solid #1e2d47 !important;
    margin: 1.5rem 0 !important;
}

/* ─── Streamlit metric widget overrides ──────────────────────────────────── */
[data-testid="stMetric"] {
    background: #131929 !important;
    border-radius: 8px !important;
    padding: 1rem !important;
}

/* ─── Dataframe ──────────────────────────────────────────────────────────── */
[data-testid="stDataFrame"] {
    background: #131929 !important;
    border-radius: 8px !important;
    border: 1px solid #1e2d47 !important;
}

/* ─── Expander ───────────────────────────────────────────────────────────── */
[data-testid="stExpander"] {
    background: #131929 !important;
    border: 1px solid #1e2d47 !important;
    border-radius: 8px !important;
}

details summary {
    color: #00d4ff !important;
    font-weight: 600 !important;
}

/* ─── Buttons ────────────────────────────────────────────────────────────── */
[data-testid="stButton"] button {
    background: linear-gradient(135deg, #00d4ff22, #0066ff22) !important;
    color: #00d4ff !important;
    border: 1px solid #00d4ff55 !important;
    border-radius: 6px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
    transition: all 0.2s ease !important;
}

[data-testid="stButton"] button:hover {
    background: linear-gradient(135deg, #00d4ff44, #0066ff44) !important;
    border-color: #00d4ff !important;
    box-shadow: 0 0 12px #00d4ff44 !important;
}

/* ─── Spinner ────────────────────────────────────────────────────────────── */
[data-testid="stSpinner"] {
    color: #00d4ff !important;
}

/* ─── Scrollbar ──────────────────────────────────────────────────────────── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #0a0e1a; }
::-webkit-scrollbar-thumb { background: #1e2d47; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #00d4ff44; }

/* ─── KPI card ───────────────────────────────────────────────────────────── */
.kpi-card {
    background: #131929;
    border-radius: 10px;
    padding: 1.2rem 1.4rem;
    border-left: 3px solid;
    position: relative;
    overflow: hidden;
    transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.kpi-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
}
.kpi-card::after {
    content: '';
    position: absolute;
    top: 0; right: 0;
    width: 60px; height: 60px;
    border-radius: 50%;
    opacity: 0.07;
    transform: translate(20px, -20px);
}
.kpi-number {
    font-size: 2rem;
    font-weight: 800;
    line-height: 1;
    font-family: 'JetBrains Mono', monospace;
    letter-spacing: -1px;
}
.kpi-label {
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    color: #7a8ba8;
    margin-top: 0.4rem;
}
.kpi-delta {
    font-size: 0.75rem;
    margin-top: 0.5rem;
    font-weight: 500;
}

/* ─── Agent card ─────────────────────────────────────────────────────────── */
.agent-card {
    background: #131929;
    border-radius: 10px;
    padding: 1rem 1.1rem;
    border: 1px solid #1e2d47;
    position: relative;
    overflow: hidden;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.agent-card:hover {
    border-color: #00d4ff44;
    box-shadow: 0 4px 16px rgba(0, 212, 255, 0.08);
}
.agent-card-top {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.5rem;
}
.agent-icon {
    font-size: 1.2rem;
}
.agent-name {
    font-size: 0.85rem;
    font-weight: 700;
    color: #c8d8f0;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.agent-badge {
    margin-left: auto;
    font-size: 0.65rem;
    font-weight: 700;
    padding: 0.15rem 0.5rem;
    border-radius: 20px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}
.badge-complete {
    background: #2ed57322;
    color: #2ed573;
    border: 1px solid #2ed57344;
}
.badge-running {
    background: #ffa50222;
    color: #ffa502;
    border: 1px solid #ffa50244;
}
.badge-failed {
    background: #ff475722;
    color: #ff4757;
    border: 1px solid #ff475744;
}
.agent-finding {
    font-size: 0.75rem;
    color: #7a8ba8;
    line-height: 1.4;
    font-style: italic;
}
.agent-status-icon {
    font-size: 0.9rem;
}

/* ─── Timeline ───────────────────────────────────────────────────────────── */
.timeline-container {
    position: relative;
    padding-left: 2rem;
}
.timeline-container::before {
    content: '';
    position: absolute;
    left: 7px;
    top: 8px;
    bottom: 8px;
    width: 2px;
    background: linear-gradient(180deg, #ff4757, #ffa502, #00d4ff, #2ed573);
    opacity: 0.4;
}
.timeline-item {
    position: relative;
    margin-bottom: 1rem;
    padding: 0.6rem 0.8rem;
    background: #131929;
    border-radius: 8px;
    border: 1px solid #1a2540;
}
.timeline-dot {
    position: absolute;
    left: -1.65rem;
    top: 50%;
    transform: translateY(-50%);
    width: 10px;
    height: 10px;
    border-radius: 50%;
    border: 2px solid #0a0e1a;
}
.timeline-time {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.65rem;
    font-weight: 500;
    margin-bottom: 0.2rem;
}
.timeline-desc {
    font-size: 0.8rem;
    color: #c8d8f0;
    line-height: 1.4;
}

/* ─── Root cause alert ───────────────────────────────────────────────────── */
.root-cause-block {
    background: linear-gradient(135deg, #1a0a0a, #1a1000);
    border: 1px solid #ff475744;
    border-left: 4px solid #ff4757;
    border-radius: 10px;
    padding: 1.5rem;
    position: relative;
    overflow: hidden;
}
.root-cause-block::before {
    content: '⚠';
    position: absolute;
    right: 1rem;
    top: 1rem;
    font-size: 3rem;
    opacity: 0.08;
}
.root-cause-title {
    font-size: 0.65rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: #ff4757;
    margin-bottom: 0.8rem;
}
.root-cause-body {
    font-size: 0.9rem;
    color: #f0d0d0;
    line-height: 1.7;
}
.root-cause-detail {
    margin-top: 0.8rem;
    padding-top: 0.8rem;
    border-top: 1px solid #ff475722;
    font-size: 0.8rem;
    color: #ffa50299;
    font-family: 'JetBrains Mono', monospace;
}

/* ─── Alarm card ─────────────────────────────────────────────────────────── */
.alarm-card {
    background: #131929;
    border-radius: 10px;
    padding: 1.2rem 1.4rem;
    border: 1px solid #1e2d47;
    transition: border-color 0.2s ease;
}
.alarm-card:hover {
    border-color: #1e3d6a;
}
.alarm-card-header {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    margin-bottom: 0.6rem;
}
.alarm-name {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    font-weight: 500;
    color: #a0b8d8;
    word-break: break-all;
}
.alarm-state-ok {
    font-size: 0.65rem;
    font-weight: 700;
    padding: 0.15rem 0.6rem;
    border-radius: 20px;
    background: #2ed57322;
    color: #2ed573;
    border: 1px solid #2ed57344;
    white-space: nowrap;
}
.alarm-state-alarm {
    font-size: 0.65rem;
    font-weight: 700;
    padding: 0.15rem 0.6rem;
    border-radius: 20px;
    background: #ff475722;
    color: #ff4757;
    border: 1px solid #ff475744;
    white-space: nowrap;
}
.alarm-state-insufficient {
    font-size: 0.65rem;
    font-weight: 700;
    padding: 0.15rem 0.6rem;
    border-radius: 20px;
    background: #ffa50222;
    color: #ffa502;
    border: 1px solid #ffa50244;
    white-space: nowrap;
}
.alarm-desc {
    font-size: 0.75rem;
    color: #5a6a88;
    line-height: 1.5;
}

/* ─── Section header ─────────────────────────────────────────────────────── */
.section-header {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    margin-bottom: 1.2rem;
    padding-bottom: 0.6rem;
    border-bottom: 1px solid #1e2d47;
}
.section-icon {
    font-size: 1rem;
    width: 28px;
    height: 28px;
    background: linear-gradient(135deg, #00d4ff22, #0066ff22);
    border: 1px solid #00d4ff33;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
}
.section-title {
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: #00d4ff;
}

/* ─── Platform header bar ────────────────────────────────────────────────── */
.platform-header {
    background: linear-gradient(90deg, #0d1224 0%, #131929 50%, #0d1224 100%);
    border: 1px solid #1e2d47;
    border-radius: 12px;
    padding: 1.2rem 2rem;
    margin-bottom: 1.5rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.platform-title {
    font-size: 1.4rem;
    font-weight: 800;
    background: linear-gradient(90deg, #00d4ff, #0066ff);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    letter-spacing: -0.5px;
}
.platform-subtitle {
    font-size: 0.72rem;
    color: #5a6a88;
    text-transform: uppercase;
    letter-spacing: 2px;
    margin-top: 0.2rem;
}
.live-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.65rem;
    font-weight: 700;
    color: #2ed573;
    background: #2ed57322;
    border: 1px solid #2ed57344;
    padding: 0.3rem 0.8rem;
    border-radius: 20px;
    text-transform: uppercase;
    letter-spacing: 1px;
}
.live-dot {
    width: 6px;
    height: 6px;
    background: #2ed573;
    border-radius: 50%;
    animation: pulse 2s infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.4; transform: scale(0.8); }
}

/* ─── Footer ─────────────────────────────────────────────────────────────── */
.platform-footer {
    background: #0d1224;
    border: 1px solid #1e2d47;
    border-radius: 8px;
    padding: 0.8rem 1.5rem;
    margin-top: 2rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.5rem;
}
.footer-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.68rem;
    color: #3a4a60;
    font-family: 'JetBrains Mono', monospace;
}
.footer-item span.val {
    color: #2ed573;
    font-weight: 600;
}
.footer-sep {
    color: #1e2d47;
}

/* ─── Sidebar ────────────────────────────────────────────────────────────── */
.sidebar-logo {
    font-size: 1.1rem;
    font-weight: 800;
    background: linear-gradient(90deg, #00d4ff, #0066ff);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    letter-spacing: 1px;
    margin-bottom: 0.2rem;
}
.sidebar-tagline {
    font-size: 0.62rem;
    color: #3a4a60;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    margin-bottom: 1rem;
}
.sidebar-clock {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.4rem;
    font-weight: 500;
    color: #00d4ff;
    letter-spacing: 2px;
}
.sidebar-date {
    font-size: 0.7rem;
    color: #5a6a88;
    margin-bottom: 1.2rem;
}
.sidebar-info-row {
    display: flex;
    justify-content: space-between;
    font-size: 0.68rem;
    padding: 0.4rem 0;
    border-bottom: 1px solid #1a2540;
}
.sidebar-info-label { color: #3a4a60; }
.sidebar-info-val { color: #a0b8d8; font-family: 'JetBrains Mono', monospace; }

/* ─── Info warning ───────────────────────────────────────────────────────── */
[data-testid="stAlert"] {
    background: #131929 !important;
    border-radius: 8px !important;
    border: 1px solid #1e2d47 !important;
}
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
    <div style="font-size:0.65rem;color:#2a3a50;text-align:center;line-height:1.8;">
        Sigma DataTech · Day 12<br>
        Multi-Agent Intelligence<br>
        Platform v1.0.0
    </div>
    """, unsafe_allow_html=True)


# ── Platform Header ───────────────────────────────────────────────────────────
st.markdown(f"""
<div class="platform-header">
    <div>
        <div class="platform-title">⚡ SIGMA INTELLIGENCE PLATFORM</div>
        <div class="platform-subtitle">Autonomous Incident Recovery Command Center</div>
    </div>
    <div style="text-align:right;">
        <div class="live-badge">
            <div class="live-dot"></div> LIVE
        </div>
        <div style="font-size:0.65rem;color:#3a4a60;margin-top:0.4rem;
                    font-family:'JetBrains Mono',monospace;">
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
    {
        "label":      "EXPECTED TRANSACTIONS",
        "value":      f"{KPI_EXPECTED:,}",
        "delta":      "Baseline volume",
        "color":      "#00d4ff",
        "delta_color":"#5a6a88",
    },
    {
        "label":      "ACTUAL TRANSACTIONS",
        "value":      f"{KPI_ACTUAL:,}",
        "delta":      f"▼ {((KPI_EXPECTED - KPI_ACTUAL) / KPI_EXPECTED * 100):.0f}% below expected",
        "color":      "#ff4757",
        "delta_color":"#ff4757",
    },
    {
        "label":      "MISSING TRANSACTIONS",
        "value":      f"{KPI_MISSING:,}",
        "delta":      "Gap detected by platform",
        "color":      "#ffa502",
        "delta_color":"#ffa502",
    },
    {
        "label":      "RECORDS RECOVERED",
        "value":      f"{KPI_RECOVERED:,}",
        "delta":      "▲ Loaded to Snowflake · 0 duplicates",
        "color":      "#2ed573",
        "delta_color":"#2ed573",
    },
    {
        "label":      "QUARANTINED",
        "value":      f"{KPI_QUARANTINED:,}",
        "delta":      "Data quality issues flagged",
        "color":      "#ffa502",
        "delta_color":"#7a8ba8",
    },
    {
        "label":      "RECOVERY TIME",
        "value":      f"{KPI_RECOVERY_SEC}s",
        "delta":      "End-to-end · 0 human interventions",
        "color":      "#2ed573",
        "delta_color":"#2ed573",
    },
]

cols = st.columns(6)
for col, kpi in zip(cols, kpis):
    with col:
        st.markdown(f"""
        <div class="kpi-card" style="border-left-color:{kpi['color']};">
            <div class="kpi-number" style="color:{kpi['color']};">{kpi['value']}</div>
            <div class="kpi-label">{kpi['label']}</div>
            <div class="kpi-delta" style="color:{kpi['delta_color']};">{kpi['delta']}</div>
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
    badge_icon  = "✅" if agent["status"] == "complete" else ("⚡" if agent["status"] == "running" else "❌")
    badge_text  = agent["status"].upper()
    with col:
        st.markdown(f"""
        <div class="agent-card">
            <div class="agent-card-top">
                <span class="agent-icon">{agent['icon']}</span>
                <span class="agent-name">{agent['name']}</span>
            </div>
            <div style="display:flex;align-items:center;gap:0.4rem;margin-bottom:0.5rem;">
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

    timeline_html = '<div class="timeline-container">'
    for ts, desc, severity in TIMELINE:
        dot_color = SEVERITY_COLORS.get(severity, "#5a6a88")
        time_color = SEVERITY_COLORS.get(severity, "#5a6a88")
        timeline_html += f"""
        <div class="timeline-item">
            <div class="timeline-dot" style="background:{dot_color};"></div>
            <div class="timeline-time" style="color:{time_color};">{ts}</div>
            <div class="timeline-desc">{desc}</div>
        </div>
        """
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

    st.markdown(f"""
    <div class="root-cause-block">
        <div class="root-cause-title">⚠ Critical Root Cause Identified</div>
        <div class="root-cause-body">{root_cause_text}</div>
        <div class="root-cause-detail">
            COMPONENT  : sigma-data-producer Lambda<br>
            EVENT      : Version v1 → v2 deployment at 02:11 UTC<br>
            MECHANISM  : Field rename order_value → transaction_amount<br>
            FAILURE    : Snowflake COPY INTO — silent null column drop<br>
            DETECTION  : Sigma Intelligence Platform autonomous scan<br>
            RESOLUTION : 61 seconds — 0 human interventions
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Fix applied box
    fix_text = data["fix_applied"] if data["fix_applied"] != "—" else (
        "Rollback Agent reverted `sigma-data-producer` from v2 to v1. "
        "Recovery Agent replayed 847 missing records into Snowflake using idempotent MERGE. "
        "Hardening Agent deployed 3 CloudWatch alarms to prevent recurrence."
    )
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,#0a1a0a,#0a1500);
                border:1px solid #2ed57344;border-left:4px solid #2ed573;
                border-radius:10px;padding:1.2rem 1.4rem;">
        <div style="font-size:0.65rem;font-weight:700;text-transform:uppercase;
                    letter-spacing:2px;color:#2ed573;margin-bottom:0.6rem;">
            ✅ Fix Applied
        </div>
        <div style="font-size:0.85rem;color:#c8f0d8;line-height:1.6;">
            {fix_text}
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

with col_donut:
    fig = go.Figure(data=[go.Pie(
        labels=["Recovered to Snowflake", "Quarantined", "Still Missing"],
        values=[KPI_RECOVERED, KPI_QUARANTINED, KPI_MISSING - KPI_RECOVERED - KPI_QUARANTINED],
        hole=0.72,
        marker=dict(
            colors=["#2ed573", "#ffa502", "#ff4757"],
            line=dict(color="#0a0e1a", width=3),
        ),
        textinfo="none",
        hovertemplate="<b>%{label}</b><br>%{value:,} records<br>%{percent}<extra></extra>",
    )])

    fig.add_annotation(
        text=f"<b>{KPI_RECOVERED:,}</b><br><span style='font-size:11px'>Recovered</span>",
        x=0.5, y=0.5,
        font=dict(size=22, color="#2ed573", family="JetBrains Mono"),
        showarrow=False,
        align="center",
    )

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#7a8ba8", family="Inter"),
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
        ("Total Missing",          f"{KPI_MISSING:,}",   "#ff4757"),
        ("Recovered",              f"{KPI_RECOVERED:,}",  "#2ed573"),
        ("Quarantined",            f"{KPI_QUARANTINED:,}", "#ffa502"),
        ("Recovery Rate",          f"{KPI_RECOVERED / KPI_MISSING * 100:.1f}%", "#00d4ff"),
        ("Duplicates Inserted",    "0",                   "#2ed573"),
        ("Time to Recovery",       f"{KPI_RECOVERY_SEC}s","#2ed573"),
    ]

    stat_pairs = [stats[i:i+2] for i in range(0, len(stats), 2)]
    for pair in stat_pairs:
        subcols = st.columns(2)
        for sc, (label, val, color) in zip(subcols, pair):
            with sc:
                st.markdown(f"""
                <div style="background:#131929;border:1px solid #1e2d47;border-radius:8px;
                            padding:0.8rem 1rem;margin-bottom:0.6rem;">
                    <div style="font-family:'JetBrains Mono',monospace;font-size:1.3rem;
                                font-weight:700;color:{color};">{val}</div>
                    <div style="font-size:0.65rem;font-weight:600;text-transform:uppercase;
                                letter-spacing:1px;color:#3a4a60;margin-top:0.25rem;">{label}</div>
                </div>
                """, unsafe_allow_html=True)

    st.markdown("""
    <div style="background:linear-gradient(90deg,#0a1a0a,#0d1a10);
                border:1px solid #2ed57322;border-radius:8px;padding:0.7rem 1rem;
                font-size:0.75rem;color:#2ed573;margin-top:0.4rem;">
        🛡 Idempotent MERGE used — duplicate insertion impossible
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

alarm_cols = st.columns(3)
for col, alarm in zip(alarm_cols, alarm_display):
    state = alarm["state"]
    if state == "OK":
        state_class = "alarm-state-ok"
        state_label = "🟢 OK"
    elif state == "ALARM":
        state_class = "alarm-state-alarm"
        state_label = "🔴 ALARM"
    else:
        state_class = "alarm-state-insufficient"
        state_label = "🟡 NO DATA"

    with col:
        st.markdown(f"""
        <div class="alarm-card">
            <div class="alarm-card-header">
                <div style="font-size:1.2rem;">🔔</div>
                <span class="{state_class}">{state_label}</span>
            </div>
            <div class="alarm-name">{alarm['name']}</div>
            <div class="alarm-desc" style="margin-top:0.5rem;">{alarm['desc']}</div>
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
