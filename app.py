"""
EdgeOps Command Agent — Streamlit application.

Run locally:
    streamlit run app.py

The UI is organized as seven tabs that mirror the demo narrative:

    Command Center → Data Upload → Signal Analysis → Vision Inspection
    → Agent Reasoning → Work Order → Management Report
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from PIL import Image
from plotly.subplots import make_subplots

from src import (
    agents,
    blob_store,
    business_case,
    cosmos_store,
    equipment_catalog,
    iot_ingest,
    rag,
    raw_ingest,
    report_generator,
    risk_engine,
    signal_analysis,
    teams_notify,
    utils,
)
from src.rag import build_query_from_findings, search as manual_search
from src.signal_analysis import SignalFeatures, spectrum_for_plot
from src.risk_engine import RiskAssessment


# ───────────────────────────────────────────────────────────────────────────
# Page config + global styles
# ───────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="EdgeOps Command Agent",
    page_icon="🛠",
    layout="wide",
    initial_sidebar_state="expanded",
)


RISK_COLORS = {
    "Normal": "#16a34a",
    "Warning": "#f59e0b",
    "Critical": "#dc2626",
}

RISK_PILL_BG = {
    "Normal":   ("#dcfce7", "#166534"),
    "Warning":  ("#fef3c7", "#92400e"),
    "Critical": ("#fee2e2", "#b91c1c"),
}


def _inject_css() -> None:
    """Fluent-ish design tokens applied globally. Run once at the top of main()."""
    st.markdown(
        """
<style>
:root {
  --eo-bg: #f5f7fa;
  --eo-card: #ffffff;
  --eo-border: #e5e7eb;
  --eo-text: #1f2937;
  --eo-muted: #6b7280;
  --eo-accent: #2563eb;
  --eo-shadow: 0 8px 24px rgba(15, 23, 42, 0.05);
}
html, body, [data-testid="stAppViewContainer"] { background: var(--eo-bg); }
[data-testid="stSidebar"][aria-expanded="true"] { min-width: 340px !important; max-width: 340px !important; }
[data-testid="stSidebar"][aria-expanded="true"] > div:first-child { min-width: 340px !important; max-width: 340px !important; }
[data-testid="stSidebar"] > div:not([data-testid="stSidebarContent"]) { display: none !important; }
.block-container {
  padding-top: 1.4rem;
  padding-bottom: 2.4rem;
  max-width: 1600px;
}
h1, h2, h3 { color: var(--eo-text); letter-spacing: -0.01em; }
h1 { font-size: 30px; font-weight: 800; }
h2 { font-size: 20px; font-weight: 700; margin-top: 1.4rem; }
h3 { font-size: 17px; font-weight: 700; }
p, li, span, div { color: var(--eo-text); }
.stCaption, [data-testid="stCaptionContainer"] { color: var(--eo-muted); }

div[data-testid="stMetric"] {
  background: var(--eo-card);
  border: 1px solid var(--eo-border);
  padding: 16px 18px;
  border-radius: 14px;
  box-shadow: var(--eo-shadow);
}
div[data-testid="stMetric"] label { color: var(--eo-muted); font-size: 12px; }
div[data-testid="stMetric"] [data-testid="stMetricValue"] {
  font-size: 22px; font-weight: 700; color: var(--eo-text);
}

.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs [data-baseweb="tab"] {
  background: transparent;
  border-radius: 10px 10px 0 0;
  padding: 10px 14px;
  font-weight: 600;
  color: var(--eo-muted);
}
.stTabs [aria-selected="true"] {
  background: var(--eo-card);
  color: var(--eo-text);
  border: 1px solid var(--eo-border);
  border-bottom-color: var(--eo-card);
}

button[kind="primary"], button[kind="secondary"] {
  border-radius: 10px !important;
  font-weight: 600 !important;
}

/* EdgeOps custom blocks */
.eo-hero {
  background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%);
  color: #ffffff;
  padding: 26px 30px;
  border-radius: 20px;
  margin-bottom: 18px;
  box-shadow: 0 18px 44px rgba(15, 23, 42, 0.22);
}
.eo-hero, .eo-hero div, .eo-hero span, .eo-hero p, .eo-hero li { color: #ffffff; }
.eo-hero .eo-pill { color: #0f172a !important; }
.eo-hero .eo-hero-eyebrow { font-size: 12px; letter-spacing: 0.12em; text-transform: uppercase; opacity: 0.75; }
.eo-hero .eo-hero-title { font-size: 30px; font-weight: 800; margin-top: 4px; }
.eo-hero .eo-hero-sub { font-size: 14px; opacity: 0.85; margin-top: 6px; }
.eo-hero .eo-hero-score { font-size: 40px; font-weight: 800; line-height: 1; }
.eo-hero .eo-hero-score-label { font-size: 12px; opacity: 0.75; margin-top: 4px; }

.eo-pill {
  display: inline-block;
  padding: 4px 12px;
  border-radius: 9999px;
  font-weight: 700;
  font-size: 12px;
  letter-spacing: 0.02em;
}
.eo-pill-critical { background: #fee2e2; color: #b91c1c; }
.eo-pill-warning  { background: #fef3c7; color: #92400e; }
.eo-pill-normal   { background: #dcfce7; color: #166534; }
.eo-pill-muted    { background: #f1f5f9; color: #475569; }

.eo-card {
  background: var(--eo-card);
  border: 1px solid var(--eo-border);
  border-radius: 16px;
  padding: 18px 20px;
  box-shadow: var(--eo-shadow);
  margin-bottom: 14px;
}
.eo-card-title { font-size: 12px; color: var(--eo-muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }
.eo-card ul { margin: 0; padding-left: 18px; }
.eo-card li { font-size: 13px; line-height: 1.55; }

.eo-agent-step {
  position: relative;
  background: var(--eo-card);
  border: 1px solid var(--eo-border);
  border-left: 4px solid var(--eo-accent);
  border-radius: 14px;
  padding: 14px 18px 14px 22px;
  margin-bottom: 10px;
  box-shadow: var(--eo-shadow);
}
.eo-agent-step.done   { border-left-color: #16a34a; }
.eo-agent-step.error  { border-left-color: #dc2626; }
.eo-agent-step.idle   { border-left-color: #94a3b8; opacity: 0.7; }
.eo-agent-step-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  font-weight: 700; font-size: 14px;
}
.eo-agent-step-desc { color: var(--eo-muted); font-size: 12.5px; margin-top: 4px; }
.eo-agent-status {
  font-size: 11px; font-weight: 700; letter-spacing: 0.04em;
  padding: 3px 10px; border-radius: 9999px;
}
.eo-agent-status.done  { background: #dcfce7; color: #166534; }
.eo-agent-status.error { background: #fee2e2; color: #b91c1c; }
.eo-agent-status.idle  { background: #f1f5f9; color: #64748b; }
.eo-agent-step-preview {
  margin-top: 10px;
  padding: 10px 12px;
  background: #f8fafc;
  border-radius: 8px;
  font-size: 13px;
  color: var(--eo-text);
  line-height: 1.55;
  border: 1px solid #eef2f7;
}
.eo-agent-step-preview .eo-trust-tag { margin-right: 8px; }
.eo-agent-thumbs { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.eo-agent-thumbs img { width: 64px; height: 48px; object-fit: cover; border-radius: 6px; border: 1px solid var(--eo-border); }

/* Section / sub-section labels */
.eo-section-eyebrow {
  font-size: 11px; font-weight: 700; letter-spacing: 0.12em;
  text-transform: uppercase; color: var(--eo-muted); margin-top: 18px; margin-bottom: 6px;
}

/* Chips (tools / parts / tags) */
.eo-chip-row { display: flex; flex-wrap: wrap; gap: 6px; }
.eo-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 5px 12px;
  background: #f1f5f9;
  color: #1e293b;
  border: 1px solid #e2e8f0;
  border-radius: 9999px;
  font-size: 12px;
  font-weight: 500;
}
.eo-chip.eo-chip-tool { background: #eff6ff; border-color: #bfdbfe; color: #1d4ed8; }
.eo-chip.eo-chip-part { background: #f5f3ff; border-color: #ddd6fe; color: #6d28d9; }

/* Callouts */
.eo-callout {
  border-left: 4px solid var(--eo-accent);
  background: #f8fafc;
  border-radius: 0 12px 12px 0;
  padding: 12px 16px;
  margin-bottom: 10px;
  font-size: 13.5px;
  line-height: 1.55;
}
.eo-callout-warning { border-left-color: #f59e0b; background: #fffbeb; }
.eo-callout-danger  { border-left-color: #dc2626; background: #fef2f2; }
.eo-callout-success { border-left-color: #16a34a; background: #f0fdf4; }
.eo-callout-info    { border-left-color: #2563eb; background: #eff6ff; }
.eo-callout-title   { font-weight: 700; margin-bottom: 4px; font-size: 12.5px; letter-spacing: 0.02em; }

/* Trust-signal tags — surface "where did this come from?" alongside content */
.eo-trust-tag {
  display: inline-block;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 2px 8px;
  border-radius: 6px;
  margin-right: 6px;
  vertical-align: middle;
}
.eo-trust-observed { background: #ecfeff; color: #0e7490; }
.eo-trust-ai       { background: #ede9fe; color: #6d28d9; }
.eo-trust-manual   { background: #ffedd5; color: #9a3412; }
.eo-trust-uncert   { background: #fef9c3; color: #854d0e; }
.eo-trust-human    { background: #fce7f3; color: #be185d; }

/* Numbered checklist (work steps) */
.eo-checklist { display: flex; flex-direction: column; gap: 8px; }
.eo-checklist-item {
  display: flex; align-items: flex-start; gap: 12px;
  background: var(--eo-card);
  border: 1px solid var(--eo-border);
  border-radius: 12px;
  padding: 12px 16px;
  box-shadow: var(--eo-shadow);
  transition: transform 120ms ease, box-shadow 120ms ease;
}
.eo-checklist-item:hover { transform: translateY(-1px); box-shadow: 0 12px 28px rgba(15,23,42,0.08); }
.eo-checklist-num {
  flex: 0 0 28px; width: 28px; height: 28px;
  background: linear-gradient(135deg, #2563eb, #1e3a8a);
  color: #ffffff;
  border-radius: 8px;
  display: inline-flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 13px;
}
.eo-checklist-text { font-size: 13.5px; line-height: 1.55; color: var(--eo-text); flex: 1; }

/* KPI strip (Management Report top) */
.eo-kpi-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 8px 0 14px; }
.eo-kpi {
  background: var(--eo-card);
  border: 1px solid var(--eo-border);
  border-radius: 14px;
  padding: 14px 16px;
  box-shadow: var(--eo-shadow);
}
.eo-kpi-label { font-size: 11px; color: var(--eo-muted); text-transform: uppercase; letter-spacing: 0.08em; }
.eo-kpi-value { font-size: 22px; font-weight: 800; color: var(--eo-text); margin-top: 4px; line-height: 1.1; }
.eo-kpi-sub   { font-size: 11px; color: var(--eo-muted); margin-top: 2px; }
@media (max-width: 880px) { .eo-kpi-strip { grid-template-columns: repeat(2, 1fr); } }

/* Empty states */
.eo-empty-state {
  background: var(--eo-card);
  border: 1px dashed #cbd5e1;
  border-radius: 16px;
  padding: 36px 24px;
  text-align: center;
  color: var(--eo-muted);
}
.eo-empty-icon { font-size: 36px; margin-bottom: 8px; opacity: 0.6; }
.eo-empty-title { font-size: 15px; font-weight: 700; color: var(--eo-text); margin-bottom: 4px; }
.eo-empty-hint { font-size: 12.5px; }

/* Sidebar workflow stepper */
[data-testid="stSidebar"] { background: #ffffff; }
[data-testid="stSidebar"] h3, [data-testid="stSidebar"] h4 { color: var(--eo-text); }
.eo-stepper { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }
.eo-stepper-item {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid transparent;
  font-size: 13px;
  transition: background 120ms ease;
}
.eo-stepper-item.done    { background: #f0fdf4; border-color: #bbf7d0; }
.eo-stepper-item.current { background: #eff6ff; border-color: #bfdbfe; }
.eo-stepper-item.pending { background: transparent; color: var(--eo-muted); }
.eo-stepper-dot {
  width: 22px; height: 22px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 800; color: white; flex-shrink: 0;
}
.eo-stepper-item.done    .eo-stepper-dot { background: #16a34a; }
.eo-stepper-item.current .eo-stepper-dot { background: #2563eb; box-shadow: 0 0 0 4px rgba(37,99,235,0.18); }
.eo-stepper-item.pending .eo-stepper-dot { background: #cbd5e1; color: #475569; }
.eo-stepper-label { font-weight: 600; }
.eo-stepper-sub { color: var(--eo-muted); font-size: 11px; font-weight: 500; }

/* Action panel — the persistent "next best action" panel */
.eo-action-panel {
  background: linear-gradient(135deg, #f8fafc 0%, #eff6ff 100%);
  border: 1px solid #bfdbfe;
  border-radius: 18px;
  padding: 20px 22px;
  box-shadow: 0 10px 28px rgba(37, 99, 235, 0.08);
  margin: 16px 0;
}
.eo-action-title { font-size: 12px; font-weight: 700; color: #1e40af; letter-spacing: 0.1em; text-transform: uppercase; }
.eo-action-headline { font-size: 17px; font-weight: 700; color: var(--eo-text); margin: 4px 0 10px; }
.eo-action-meta { color: var(--eo-muted); font-size: 12.5px; }

/* Flow-step header (numbered process) */
.eo-flow-step {
  display: flex; align-items: center; gap: 14px;
  margin: 26px 0 12px;
  padding: 14px 18px;
  background: linear-gradient(135deg, #ffffff, #f0f9ff);
  border: 1px solid #bfdbfe;
  border-left: 5px solid #2563eb;
  border-radius: 14px;
  box-shadow: var(--eo-shadow);
}
.eo-flow-step-num {
  flex: 0 0 38px; width: 38px; height: 38px;
  background: linear-gradient(135deg, #2563eb, #1e3a8a);
  color: white;
  border-radius: 10px;
  display: inline-flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: 17px;
  box-shadow: 0 6px 16px rgba(37, 99, 235, 0.28);
}
.eo-flow-step-title { font-size: 16px; font-weight: 800; color: var(--eo-text); line-height: 1.2; }
.eo-flow-step-sub { font-size: 12.5px; color: var(--eo-muted); margin-top: 2px; line-height: 1.45; }

.eo-trust-row {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  margin-bottom: 8px;
}
.eo-flow-sublabel { font-size: 12px; color: var(--eo-muted); font-weight: 600; }

/* Animations */
@keyframes critical-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0.45); }
  50%      { box-shadow: 0 0 0 8px rgba(220, 38, 38, 0.0); }
}
.eo-pill-critical { animation: critical-pulse 2.2s ease-in-out infinite; }
@keyframes eo-fade-in {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}
.eo-hero, .eo-card, .eo-callout, .eo-checklist-item, .eo-agent-step, .eo-kpi, .eo-action-panel {
  animation: eo-fade-in 380ms ease both;
}

/* Sidebar — selected equipment block: dark-navy left border, readable bg */
[data-testid="stSidebar"] .stButton button[kind="primary"]:disabled,
[data-testid="stSidebar"] .stButton button[kind="primary"][disabled] {
  background: #ffffff !important;
  color: var(--eo-text) !important;
  border: 1px solid var(--eo-border) !important;
  border-left: 4px solid #1e3a8a !important;
  opacity: 1 !important;
  cursor: default !important;
  box-shadow: 0 4px 14px rgba(30, 58, 138, 0.10) !important;
}
[data-testid="stSidebar"] .stButton button[kind="primary"]:disabled *,
[data-testid="stSidebar"] .stButton button[kind="primary"][disabled] * {
  color: var(--eo-text) !important;
}

/* Scenario-strength buttons: equal height, no wrap, no clipping */
[class*="st-key-preset_"] button {
  min-height: 42px !important;
  white-space: nowrap !important;
  overflow: visible !important;
  padding-left: 4px !important;
  padding-right: 4px !important;
  font-size: 12px !important;
}
[class*="st-key-preset_"] button p,
[class*="st-key-preset_"] button div {
  margin: 0 !important;
  line-height: 1.2 !important;
  white-space: nowrap !important;
  overflow: visible !important;
  text-overflow: clip !important;
}

/* Top-right main menu: swap kebab (⋮) for a gear (⚙) */
[data-testid="stMainMenu"] button svg { display: none !important; }
[data-testid="stMainMenu"] button::before {
  content: "⚙";
  font-size: 20px;
  line-height: 1;
  color: var(--eo-text);
}
</style>
        """,
        unsafe_allow_html=True,
    )


# ───────────────────────────────────────────────────────────────────────────
# Session state
# ───────────────────────────────────────────────────────────────────────────

def _init_state() -> None:
    ss = st.session_state
    ss.setdefault("sensor_df", None)
    ss.setdefault("features", None)
    ss.setdefault("risk", None)
    ss.setdefault("pipeline", None)
    ss.setdefault("image_path", None)
    ss.setdefault("inspection_memo", "")
    ss.setdefault("equipment_id", "Pump-03")
    ss.setdefault("active_preset_key", None)
    ss.setdefault("active_intensity", "normal")
    ss.setdefault("sample_rate_hz", signal_analysis.SAMPLE_RATE_HZ)
    ss.setdefault("extra_image_paths", [])
    ss.setdefault("reference_image_override", None)
    ss.setdefault("approval_status", "未承認")
    ss.setdefault("agent_log", [])
    ss.setdefault("approval_log", [])
    ss.setdefault("approval_comment", "")
    ss.setdefault("qa_history", [])


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

def _load_preset(key: str) -> None:
    preset = utils.DEMO_PRESETS[key]
    df = preset.sensor_loader()
    st.session_state.sensor_df = df
    st.session_state.image_path = preset.image_path
    st.session_state.inspection_memo = preset.inspection_memo
    st.session_state.equipment_id = preset.equipment_id
    st.session_state.active_preset_key = key
    st.session_state.active_intensity = preset.intensity
    # Demo presets are generated at the canonical demo sample rate. Reset it so
    # a prior raw-data import's custom rate doesn't leak into preset analysis.
    st.session_state.sample_rate_hz = signal_analysis.SAMPLE_RATE_HZ
    st.session_state.pipeline = None
    st.session_state.approval_status = "未承認"
    _recompute_features()


def _recompute_features() -> None:
    df = st.session_state.sensor_df
    if df is None or df.empty:
        st.session_state.features = None
        st.session_state.risk = None
        return
    fs = st.session_state.get("sample_rate_hz", signal_analysis.SAMPLE_RATE_HZ)
    features = signal_analysis.analyze(df, fs=fs)
    risk = risk_engine.assess(features, equipment_id=st.session_state.equipment_id)
    st.session_state.features = features
    st.session_state.risk = risk


def _history_summary() -> str:
    try:
        hist = pd.read_csv(utils.DATA_DIR / "failure_history.csv")
        same = hist[hist["equipment_id"] == st.session_state.equipment_id].tail(5)
        if same.empty:
            return "（同一設備の故障履歴なし）"
        lines = []
        for _, row in same.iterrows():
            lines.append(
                f"- {row['date']}: {row['risk_level']} / {row['detected_symptoms']} → "
                f"{row['root_cause']} → {row['action_taken']}（{row['downtime_hours']}h停止）"
            )
        return "\n".join(lines)
    except Exception as exc:  # pragma: no cover
        return f"（履歴読込エラー: {exc}）"


def _inventory_summary() -> str:
    try:
        inv = pd.read_csv(utils.DATA_DIR / "parts_inventory.csv")
        lines = [
            f"- {row['part_id']} {row['name']}: 在庫{row['stock']}（リードタイム{row['lead_time_days']}日）"
            for _, row in inv.head(6).iterrows()
        ]
        return "\n".join(lines)
    except Exception as exc:  # pragma: no cover
        return f"（在庫読込エラー: {exc}）"


def _run_pipeline() -> None:
    features = st.session_state.features
    risk = st.session_state.risk
    if features is None or risk is None:
        st.warning("先にセンサーデータを読み込んでください。")
        return
    st.session_state.agent_log = []
    progress_box = st.empty()
    progress_bar = st.progress(0)
    steps_total = 8  # Intake → Signal → Vision → Manual RAG → Root Cause → Action Plan → What-if → Governance
    done = {"n": 0}

    def progress(msg: str) -> None:
        done["n"] += 1
        st.session_state.agent_log.append(msg)
        progress_box.info(msg)
        progress_bar.progress(min(done["n"] / steps_total, 1.0))

    # Shared client so we can surface which orchestration path (SK vs raw SDK)
    # was actually exercised after the run.
    shared_client = agents.LLMClient() if not utils.use_mock_mode() else None

    # Pull the catalog "normal" image as a reference shot so the Vision Agent
    # can compute comparison_to_normal whenever we're not already analysing the
    # normal preset itself. A user-uploaded reference (Data Upload tab)
    # always wins over the catalog default.
    reference_path: Path | None = None
    if st.session_state.get("reference_image_override"):
        candidate = Path(st.session_state.reference_image_override)
        if candidate.exists():
            reference_path = candidate
    else:
        try:
            active_intensity = st.session_state.get("active_intensity", "normal")
            if active_intensity != "normal":
                spec = equipment_catalog.get(st.session_state.equipment_id)
                ref = spec.image_paths.get("normal")
                if ref and Path(ref).exists():
                    reference_path = Path(ref)
        except Exception:
            pass

    pipeline = agents.run_pipeline(
        features=features,
        risk=risk,
        image_path=st.session_state.image_path,
        inspection_memo=st.session_state.inspection_memo,
        history_summary=_history_summary(),
        inventory_summary=_inventory_summary(),
        progress=progress,
        client=shared_client,
        equipment_id=st.session_state.equipment_id,
        extra_image_paths=st.session_state.get("extra_image_paths"),
        reference_image_path=reference_path,
    )
    st.session_state.pipeline = pipeline
    if shared_client is not None:
        st.session_state["last_text_source"] = shared_client.last_text_source
    progress_bar.empty()
    progress_box.empty()

    # Persist a summary of this run so the Past Cases panel reflects it
    # immediately, even when Cosmos is not configured.
    try:
        cosmos_store.record_run(
            st.session_state.equipment_id,
            risk_level=risk.risk_level,
            health_score=risk.health_score,
            primary_concern=risk.primary_concern,
            summary=(pipeline.signal.output.get("summary") if isinstance(pipeline.signal.output, dict) else "") or "",
            action_plan=pipeline.action_plan.output if isinstance(pipeline.action_plan.output, dict) else None,
            root_cause=pipeline.root_cause.output if isinstance(pipeline.root_cause.output, dict) else None,
        )
    except Exception as exc:  # pragma: no cover
        st.warning(f"Cosmos への記録に失敗しました: {exc}")

    st.success("Multi-Agent 解析が完了しました。各タブで結果を確認できます。")


# ───────────────────────────────────────────────────────────────────────────
# Equipment picker — sidebar card list
# ───────────────────────────────────────────────────────────────────────────

def _equipment_picker_sidebar() -> None:
    """Vertical block list. Each block is a single button showing the kind icon,
    ID, location, and a live risk indicator. Clicking the block selects it."""
    active_id = st.session_state.equipment_id
    intensity = st.session_state.get("active_intensity", "normal")
    pipeline = st.session_state.pipeline

    risk_dot = {"Critical": "🔴", "Warning": "🟡", "Normal": "🟢"}

    for spec in equipment_catalog.list_equipment():
        try:
            df = equipment_catalog.cached_sensor_df(spec.id, intensity)
            features = signal_analysis.analyze(df)
            eq_risk = risk_engine.assess(features, equipment_id=spec.id)
            risk_level = eq_risk.risk_level
            rule_health = eq_risk.health_score
        except Exception:
            risk_level, rule_health = "Normal", 100

        # Every card shows the AI-style score (vision severity blended into
        # the rule score) — this way the column is consistent across all
        # equipment, not just the one Run Agents was clicked on.
        health = agents.predicted_ai_score(spec.id, intensity, rule_health, risk_level)
        is_active = spec.id == active_id
        # Active equipment with a fresh pipeline: use the *real* AI score
        # from the actual multi-agent run so Azure-stochastic results show.
        if is_active and pipeline is not None:
            health = pipeline.ai_health_score
        dot = risk_dot.get(risk_level, "⚪")
        label = (
            f"{spec.kind_icon} **{spec.id}**\n\n"
            f"{dot} {risk_level} · 🤖 {health}\n\n"
            f"📍 {spec.location}"
        )

        if st.button(
            label,
            key=f"pick_{spec.id}",
            use_container_width=True,
            type="primary" if is_active else "secondary",
            disabled=is_active,
        ):
            st.session_state.equipment_id = spec.id
            st.session_state.pipeline = None
            st.session_state.approval_status = "未承認"
            # Only reload a catalog preset when the current data *is* a catalog
            # preset. After a raw import ("raw:*"), CSV upload ("uploaded") or a
            # Spresense fetch ("spresense:*") — i.e. any key not in the preset
            # registry — reloading would silently overwrite the operator's own
            # data with synthetic catalog data, so keep their input.
            preset_key = st.session_state.active_preset_key
            user_supplied = bool(preset_key) and preset_key not in utils.DEMO_PRESETS
            if preset_key and not user_supplied:
                _load_preset(f"{spec.id}:{intensity}")
            elif user_supplied:
                st.toast(
                    f"投入済みデータを保持したまま {spec.id} に切替えました"
                    "（プリセット読込はスキップ）。", icon="📌",
                )
            st.rerun()

    spec = equipment_catalog.get(active_id)
    st.caption(f"{spec.description}")


# ───────────────────────────────────────────────────────────────────────────
# Sidebar
# ───────────────────────────────────────────────────────────────────────────

def _workflow_state() -> list[tuple[str, str, str]]:
    """Return the (status, label, sub) tuples for the 6-step business workflow.

    status is one of {"done", "current", "pending"}. "current" is assigned to
    the first non-done step, so the user always sees what to do next."""
    ss = st.session_state
    flags = [
        ss.sensor_df is not None,                                 # Observe
        ss.risk is not None,                                      # Detect
        ss.pipeline is not None,                                  # Diagnose
        ss.pipeline is not None,                                  # Plan
        ss.approval_status != "未承認",                            # Approve
        ss.approval_status == "承認済",                            # Report
    ]
    steps = [
        ("Observe",  "データ入力"),
        ("Detect",   "異常検知"),
        ("Diagnose", "原因推定"),
        ("Plan",     "作業指示"),
        ("Approve",  "人間承認"),
        ("Report",   "管理者報告"),
    ]
    out: list[tuple[str, str, str]] = []
    current_set = False
    for done, (label, sub) in zip(flags, steps):
        if done:
            out.append(("done", label, sub))
        elif not current_set:
            out.append(("current", label, sub))
            current_set = True
        else:
            out.append(("pending", label, sub))
    return out


def _render_workflow_stepper() -> None:
    items_html: list[str] = []
    for i, (status, label, sub) in enumerate(_workflow_state(), start=1):
        glyph = "✓" if status == "done" else str(i)
        items_html.append(
            f"<div class='eo-stepper-item {status}'>"
            f"  <span class='eo-stepper-dot'>{glyph}</span>"
            f"  <span><span class='eo-stepper-label'>{label}</span>"
            f"  <span class='eo-stepper-sub'> · {sub}</span></span>"
            f"</div>"
        )
    st.markdown(f"<div class='eo-stepper'>{''.join(items_html)}</div>", unsafe_allow_html=True)


def _sidebar() -> None:
    with st.sidebar:
        st.markdown("### 🛠 EdgeOps")
        st.caption("Maintenance Command Agent")

        # Always-visible LLM connection badge so a viewer can never mistake
        # deterministic mock output for a live Azure OpenAI run.
        if utils.use_mock_mode():
            st.markdown(
                "<div style='background:#fef3c7;border:1px solid #fcd34d;border-radius:8px;"
                "padding:6px 10px;margin:4px 0 10px;font-size:12px;font-weight:600;color:#92400e;'>"
                "🟡 モック動作中 — 実LLM未接続<br>"
                "<span style='font-weight:400;font-size:11px;'>"
                "決定的なデモ応答です（Azure OpenAI 未接続）</span></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<div style='background:#dcfce7;border:1px solid #86efac;border-radius:8px;"
                "padding:6px 10px;margin:4px 0 10px;font-size:12px;font-weight:600;color:#166534;'>"
                "🟢 Azure OpenAI 接続中 — Live AI</div>",
                unsafe_allow_html=True,
            )

        st.markdown("##### 業務フロー")
        _render_workflow_stepper()

        st.markdown("##### 設備")
        _equipment_picker_sidebar()

        st.markdown("##### シナリオ強度")
        intensities = [("normal", "🟢 Normal"), ("warning", "🟡 Warning"),
                       ("critical", "🔴 Critical")]
        cols = st.columns(3)
        for i, (intensity, label) in enumerate(intensities):
            preset_key = f"{st.session_state.equipment_id}:{intensity}"
            is_active = st.session_state.get("active_intensity") == intensity
            if cols[i].button(
                label,
                use_container_width=True,
                key=f"preset_{preset_key}",
                type="primary" if is_active else "secondary",
            ):
                _load_preset(preset_key)

        st.markdown("##### 実行")
        if st.button("▶ Run Agents", type="primary", use_container_width=True):
            _run_pipeline()

        with st.expander("動作モード / 連携サービス", expanded=False):
            in_mock = utils.use_mock_mode()
            if in_mock:
                st.caption("🟡 **Mock mode**（決定的モック応答）")
                st.caption("Azure OpenAI を使うには `.env` を設定し `EDGEOPS_USE_MOCK=false` に。")
            else:
                st.caption("🟢 **Azure OpenAI mode**（実モデル稼働中）")
            last_route = st.session_state.get("last_text_source")
            if last_route == "semantic_kernel":
                st.caption("オーケストレーション: **Semantic Kernel**")
            elif last_route == "azure_openai_sdk":
                st.caption("オーケストレーション: Azure OpenAI SDK 直叩き（SK フォールバック）")
            elif not in_mock:
                st.caption("オーケストレーション: 未実行（Run Agents で経路が決定）")
            st.caption(f"RAG: **{rag.active_backend()}**")
            st.caption(f"Blob: {'**Azure**' if blob_store.is_configured() else 'ローカル `_uploaded/`'}")
            st.caption(f"Cosmos: {'**Azure**' if cosmos_store.is_configured() else 'ローカル JSONL'}")
            st.caption(f"Teams: {'**Configured**' if teams_notify.is_configured() else '未設定'}")
            st.caption(f"Spresense: **{iot_ingest.active_source()}**")

        # AI Search ops — visible only when configured, to keep the sidebar
        # quiet during pure-local demos.
        from src import ai_search as _ai_search
        if _ai_search.is_configured():
            with st.expander("Azure AI Search", expanded=False):
                try:
                    count = _ai_search.count_docs()
                    st.caption(f"インデックス内ドキュメント数: **{count}**")
                except Exception as exc:
                    st.caption(f"カウント取得失敗: {exc}")
                if st.button("ローカルマニュアルをインデックスに投入", key="seed_search"):
                    with st.spinner("Azure AI Search に投入中…"):
                        res = rag.seed_azure_search_from_local_manual()
                    st.success(f"status={res['status']} / uploaded={res['uploaded']}")


# ───────────────────────────────────────────────────────────────────────────
# Command Center tab
# ───────────────────────────────────────────────────────────────────────────

def _tab_command_center() -> None:
    risk: RiskAssessment | None = st.session_state.risk

    if risk is None:
        st.markdown("## Command Center")
        _render_empty_state(
            icon="🏠",
            title="左サイドバーから設備とシナリオ強度を選択してください",
            hint="設備 × シナリオを選ぶと、Health Score / Risk Level / 主要懸念がここに大きく表示されます。",
        )
        return

    _render_hero_incident(risk)
    _render_ai_provenance_banner()
    _render_evidence_grid(risk)

    if risk is not None and risk.risk_level != "Normal":
        _impact_scope_card(risk)
        _teams_card_preview(risk)
        _teams_send_panel(risk)

    _past_cases_panel()


def _render_ai_provenance_banner() -> None:
    """Show that the multi-agent pipeline actually ran on Azure OpenAI (or
    fell back to mock), with timestamps and per-agent latency, so viewers can
    tell results aren't a hard-coded demo."""
    pipeline = st.session_state.pipeline
    if pipeline is None:
        return
    azure_n = pipeline.azure_count
    mock_n = pipeline.mock_count
    total_n = len(pipeline.agents)
    total_s = pipeline.total_elapsed_ms / 1000.0
    finished = (pipeline.finished_at or "").replace("T", " ")
    if azure_n == 0:
        badge_bg, badge_color, badge_text = "#fef3c7", "#92400e", "🟡 Mock fallback"
        msg = "Azure OpenAI 未接続のため全エージェントがモック応答です。"
    elif mock_n == 0:
        badge_bg, badge_color, badge_text = "#dcfce7", "#166534", "🟢 Live AI"
        msg = f"{azure_n}/{total_n} エージェントが Azure OpenAI で実行されました。"
    else:
        badge_bg, badge_color, badge_text = "#dbeafe", "#1e3a8a", "🔵 Live AI (一部 fallback)"
        msg = f"{azure_n}/{total_n} エージェントが Azure OpenAI 実行、{mock_n} 件はモック fallback。"

    agent_chips = "".join(
        f"<span style='background:{'#dcfce7' if a.source == 'azure_openai' else '#fef3c7'};"
        f"color:{'#166534' if a.source == 'azure_openai' else '#92400e'};"
        f"padding:2px 8px;border-radius:9999px;font-size:11px;margin-right:4px;'>"
        f"{a.name.replace(' Agent', '').replace(' Simulator', '')} · {a.elapsed_ms} ms</span>"
        for a in pipeline.agents
    )

    st.markdown(
        f"<div style='display:flex;align-items:center;gap:12px;flex-wrap:wrap;"
        f"background:{badge_bg};border:1px solid {badge_color}33;border-radius:10px;"
        f"padding:10px 14px;margin-bottom:12px;'>"
        f"<span style='background:{badge_color};color:#ffffff;padding:3px 10px;"
        f"border-radius:9999px;font-size:11px;font-weight:700;letter-spacing:0.04em;'>"
        f"{badge_text}</span>"
        f"<span style='font-size:13px;color:{badge_color};font-weight:600;'>{msg}</span>"
        f"<span style='font-size:12px;color:#475569;'>実行: {finished} · 合計 {total_s:.1f}s</span>"
        f"<div style='flex-basis:100%;margin-top:6px;'>{agent_chips}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _render_hero_incident(risk: RiskAssessment) -> None:
    """Big incident card at the top of Command Center: equipment, risk, score,
    primary concern, recommended deadline, and approval status — readable in
    under 10 seconds."""
    equipment_id = st.session_state.equipment_id
    pipeline = st.session_state.pipeline

    deadline_label = "未算出"
    if pipeline is not None and isinstance(pipeline.action_plan.output, dict):
        d = pipeline.action_plan.output.get("deadline_hours")
        if d is not None:
            deadline_label = f"{d}h 以内"

    approval_status = st.session_state.get("approval_status", "未承認")
    pill_class = {
        "Critical": "eo-pill-critical",
        "Warning":  "eo-pill-warning",
        "Normal":   "eo-pill-normal",
    }.get(risk.risk_level, "eo-pill-muted")

    # Always show an AI-blended score so the number is consistent with the
    # sidebar. With a fresh pipeline we use the real value from the multi-
    # agent run; otherwise we use the deterministic predictor.
    intensity = st.session_state.get("active_intensity", "normal")
    if pipeline is not None:
        ai_score = pipeline.ai_health_score
        source_label = f"🤖 AI 評価 · 推論 {pipeline.total_elapsed_ms/1000:.1f}s"
    else:
        ai_score = agents.predicted_ai_score(equipment_id, intensity, risk.health_score, risk.risk_level)
        source_label = "🤖 AI 評価 (予測) · Run Agents で詳細推論"
    delta = ai_score - risk.health_score
    delta_sign = "+" if delta > 0 else ""
    delta_color = "#86efac" if delta >= 0 else "#fca5a5"
    score_block = (
        f"<div class='eo-hero-score'>{ai_score}</div>"
        f"<div class='eo-hero-score-label'>AI Health Score / 100</div>"
        f"<div style='font-size:11px;margin-top:6px;opacity:0.85;'>"
        f"ルール {risk.health_score} → <span style='color:{delta_color};font-weight:700;'>"
        f"{delta_sign}{delta}</span></div>"
        f"<div style='font-size:10.5px;margin-top:4px;opacity:0.7;'>{source_label}</div>"
    )

    st.markdown(
        f"""
<div class='eo-hero'>
  <div style='display:flex;justify-content:space-between;align-items:flex-start;gap:24px;flex-wrap:wrap;'>
    <div style='flex:1;min-width:280px;'>
      <div class='eo-hero-eyebrow'>EdgeOps Command Center</div>
      <div class='eo-hero-title'>{equipment_id}</div>
      <div class='eo-hero-sub'>主要懸念: {risk.primary_concern}</div>
      <div style='margin-top:14px;display:flex;gap:8px;flex-wrap:wrap;'>
        <span class='eo-pill {pill_class}'>{risk.risk_level}</span>
        <span class='eo-pill eo-pill-muted'>対応期限 {deadline_label}</span>
        <span class='eo-pill eo-pill-muted'>承認: {approval_status}</span>
      </div>
    </div>
    <div style='text-align:right;min-width:180px;'>
      {score_block}
    </div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )


def _render_evidence_grid(risk: RiskAssessment) -> None:
    """Four cards under the hero: 異常根拠 / 業務影響 / 次の行動 / 承認状態."""
    pipeline = st.session_state.pipeline
    plan: dict[str, Any] = {}
    if pipeline is not None and isinstance(pipeline.action_plan.output, dict):
        plan = pipeline.action_plan.output

    # 異常根拠 — pull the non-Normal finding notes (top 4)
    evidence_lines = [n for n in risk.evidence_lines() if n][:4]
    if not evidence_lines:
        evidence_lines = ["観測値はしきい値以下。"]

    # 業務影響 — derive from risk level (matches _impact_scope_card framing)
    if risk.risk_level == "Critical":
        impact_lines = [
            "生産停止リスク: 高",
            "安全リスク: 中",
            "下流設備への波及可能性",
        ]
    elif risk.risk_level == "Warning":
        impact_lines = [
            "生産停止リスク: 中",
            "下流バッファで吸収可能",
            "悪化次第で計画外停止の可能性",
        ]
    else:
        impact_lines = ["影響なし（通常運転継続）"]

    # 次の行動 — first 3 work steps if pipeline ran, otherwise a hint
    next_actions: list[str] = []
    for raw in plan.get("work_steps", [])[:3]:
        s = str(raw).strip()
        # Strip leading "1. " / "・" so the card list looks clean
        for prefix in ("1.", "2.", "3.", "4.", "5.", "6.", "7.", "・", "-"):
            if s.startswith(prefix):
                s = s[len(prefix):].strip()
                break
        next_actions.append(s)
    if not next_actions:
        next_actions = ["Run Agents で作業指示を生成してください。"]

    # 承認状態
    approval_status = st.session_state.get("approval_status", "未承認")
    if pipeline is None:
        wo_state = "未生成"
        mr_state = "未生成"
    else:
        wo_state = approval_status
        mr_state = approval_status

    cards = [
        ("異常根拠", "eo-trust-observed", "観測事実", evidence_lines),
        ("業務影響", "eo-trust-ai",       "AI推論",   impact_lines),
        ("次の行動", "eo-trust-ai",       "AI推論",   next_actions),
        ("承認状態", "eo-trust-human",    "人間確認", [f"Work Order: {wo_state}", f"Report: {mr_state}"]),
    ]
    cols = st.columns(4)
    for col, (title, trust_cls, trust_label, lines) in zip(cols, cards):
        items = "".join(f"<li>{line}</li>" for line in lines)
        col.markdown(
            f"""
<div class='eo-card'>
  <div class='eo-card-title'><span class='eo-trust-tag {trust_cls}'>{trust_label}</span>{title}</div>
  <ul>{items}</ul>
</div>
            """,
            unsafe_allow_html=True,
        )


def _downstream_for(equipment_id: str) -> list[str]:
    """Catalog-backed downstream lookup. In production this would come from
    a real equipment master / BOM service."""
    try:
        spec = equipment_catalog.get(equipment_id)
        return list(spec.downstream) or ["（依存先未登録）"]
    except KeyError:
        return ["（依存先未登録）"]


def _impact_scope_card(risk: RiskAssessment) -> None:
    color = RISK_COLORS.get(risk.risk_level, "#94a3b8")
    equip_id = st.session_state.equipment_id
    downstream = _downstream_for(equip_id)

    plan = {}
    if st.session_state.pipeline is not None:
        raw = st.session_state.pipeline.action_plan.output
        if isinstance(raw, dict):
            plan = raw
    deadline_h = plan.get("deadline_hours")
    deadline_text = f"{deadline_h} 時間以内" if deadline_h is not None else "Run Agents 後に算出"
    production_impact = plan.get("production_impact") or ""

    if risk.risk_level == "Critical":
        scope_label = "ライン全停止リスク"
        scope_detail = "下流設備への波及で製造ライン1の全停止に至る可能性"
        impact_fallback = "計画外停止リスクが高く、復旧まで数時間〜数日の生産影響を想定"
    elif risk.risk_level == "Warning":
        scope_label = "単機影響レベル"
        scope_detail = "下流バッファで吸収可能だが、悪化次第で計画外停止リスク"
        impact_fallback = "監視継続で対応可能。悪化次第で計画外停止の可能性"
    else:
        scope_label = "影響なし"
        scope_detail = "通常運転継続"
        impact_fallback = "影響なし"

    if not production_impact.strip() or production_impact == "—":
        production_impact = impact_fallback

    downstream_html = "".join(
        f"<li style='margin-bottom:2px;'>{d}</li>" for d in downstream
    )

    # Quantified ROI — deterministic, derived from failure_history + parts_inventory.
    impact = business_case.estimate(equip_id, risk.risk_level)
    avoided_text = f"¥{impact.expected_avoided_cost:,}"
    avoided_caption = (
        f"放置時 ¥{impact.run_to_failure_cost:,} 想定 / 悪化確率 {impact.escalation_probability:.0%} 反映"
    )
    lead_warn_html = ""
    if impact.lead_time_risk and impact.parts:
        worst = next((p for p in impact.parts if p.out_of_stock or p.below_reorder), impact.parts[0])
        lead_warn_html = (
            f"<div style='margin-top:10px;background:#fef2f2;border:1px solid #fecaca;"
            f"border-radius:8px;padding:8px 10px;font-size:12px;color:#991b1b;'>"
            f"⚠ 調達リスク: {worst.name}（{worst.part_id}） {worst.availability_note}。"
            f"早期検知で前倒し手配でき、即日対応不能を回避できます。</div>"
        )

    st.markdown("### 影響範囲（インパクト分析）")
    st.markdown(
        f"<div style='border:1px solid #e2e8f0;border-radius:12px;padding:14px 16px;"
        f"background:#ffffff;box-shadow:0 1px 2px rgba(0,0,0,0.04);max-width:760px;'>"
        f"<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;'>"
        f"<div style='font-weight:600;font-size:14px;color:#0f172a;'>影響規模</div>"
        f"<div style='background:{color};color:white;padding:2px 12px;border-radius:9999px;"
        f"font-size:12px;font-weight:600;'>{scope_label}</div>"
        f"</div>"
        f"<div style='color:#475569;font-size:13px;margin-bottom:12px;'>{scope_detail}</div>"
        f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;'>"
        f"<div style='background:#f8fafc;border-radius:8px;padding:10px 12px;'>"
        f"<div style='color:#64748b;font-size:11px;margin-bottom:4px;'>想定停止対応期限</div>"
        f"<div style='font-weight:600;font-size:18px;color:#0f172a;'>{deadline_text}</div>"
        f"</div>"
        f"<div style='background:#f8fafc;border-radius:8px;padding:10px 12px;'>"
        f"<div style='color:#64748b;font-size:11px;margin-bottom:4px;'>想定生産影響</div>"
        f"<div style='font-weight:600;font-size:13px;color:#0f172a;line-height:1.4;'>{production_impact}</div>"
        f"</div>"
        f"<div style='background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;padding:10px 12px;'>"
        f"<div style='color:#047857;font-size:11px;margin-bottom:4px;'>早期介入の想定回避コスト</div>"
        f"<div style='font-weight:700;font-size:18px;color:#065f46;'>{avoided_text}</div>"
        f"<div style='color:#059669;font-size:10px;margin-top:2px;line-height:1.3;'>{avoided_caption}</div>"
        f"</div>"
        f"</div>"
        f"{lead_warn_html}"
        f"<div style='margin-top:12px;'>"
        f"<div style='color:#64748b;font-size:11px;margin-bottom:4px;'>連動・下流設備</div>"
        f"<ul style='margin:0;padding-left:18px;font-size:13px;color:#0f172a;'>{downstream_html}</ul>"
        f"</div>"
        f"<div style='margin-top:10px;color:#94a3b8;font-size:11px;'>"
        f"※ 依存マップ・金額前提はデモ用静的データ（停止コスト ¥{impact.line_stop_cost_per_hour:,}/h 想定）。"
        f"実運用では設備マスタ / BOM / 原価データ連携で取得想定。"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _teams_send_panel(risk: RiskAssessment) -> None:
    """Real Teams Incoming Webhook send. Hidden until TEAMS_WEBHOOK_URL is set."""
    st.markdown("### Teams 通知の送信")
    configured = teams_notify.is_configured()
    if not configured:
        st.info(
            "`.env` に **TEAMS_WEBHOOK_URL** を設定すると、ここから Adaptive Card を実際に送れます。"
            " Power Automate の HTTP トリガー URL でも、Teams Incoming Webhook URL でも構いません。"
        )
        return
    plan = {}
    deadline_hours = None
    if st.session_state.pipeline is not None:
        raw = st.session_state.pipeline.action_plan.output
        if isinstance(raw, dict):
            plan = raw
            deadline_hours = plan.get("deadline_hours")
    body_lines = [
        f"**主要懸念**: {risk.primary_concern}",
        f"**ヘルススコア**: {risk.health_score}/100",
    ]
    if deadline_hours is not None:
        body_lines.append(f"**対応期限**: {deadline_hours} 時間以内")
    if st.button("📣 Teams に通知を送る", key="teams_send_btn"):
        with st.spinner("Teams へ送信中…"):
            result = teams_notify.notify_alert(
                equipment_id=st.session_state.equipment_id,
                risk_level=risk.risk_level,
                health_score=risk.health_score,
                primary_concern=risk.primary_concern,
                deadline_hours=deadline_hours,
                body_lines=body_lines,
            )
        if result.ok:
            st.success(f"✅ 送信成功（{result.payload_kind}, HTTP {result.status_code}）")
        else:
            st.error(f"⛔ 送信失敗: {result.detail}")
        try:
            cosmos_store.record_alert(
                st.session_state.equipment_id,
                risk_level=risk.risk_level,
                channel="teams",
                ok=result.ok,
                detail=result.detail,
            )
        except Exception:
            pass


def _past_cases_panel() -> None:
    """Show recent runs across equipment. Pulls from Cosmos when configured,
    otherwise from the local fallback JSONL."""
    runs = cosmos_store.latest_runs_across_equipment(limit=10)
    if not runs:
        return
    st.markdown("### 過去事例（直近 10 件）")
    source_label = "Cosmos DB" if cosmos_store.is_configured() else "ローカル `_local_cosmos.jsonl`"
    st.caption(f"記録先: {source_label}")
    rows = []
    for r in runs:
        rows.append({
            "日時": r.get("timestamp", ""),
            "設備": r.get("equipment_id", ""),
            "リスク": r.get("risk_level", ""),
            "ヘルス": r.get("health_score", ""),
            "主要懸念": r.get("primary_concern", ""),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _teams_card_preview(risk: RiskAssessment) -> None:
    color = RISK_COLORS.get(risk.risk_level, "#94a3b8")
    body_lines = []
    if st.session_state.pipeline is not None:
        plan = st.session_state.pipeline.action_plan.output
        if isinstance(plan, dict):
            deadline = plan.get("deadline_hours", "?")
            body_lines.append(f"<b>対応期限</b>: {deadline} 時間以内")
    body_lines.append(f"<b>主要懸念</b>: {risk.primary_concern}")
    body_lines.append(f"<b>ヘルススコア</b>: {risk.health_score} / 100")
    body_text = " ／ ".join(body_lines)

    st.markdown("### 通知プレビュー（Microsoft Teams 連携想定）")
    st.markdown(
        f"<div style='border:1px solid #cbd5e1;border-radius:10px;padding:0;background:#f8fafc;"
        f"box-shadow:0 1px 3px rgba(0,0,0,0.06);max-width:560px;'>"
        f"<div style='background:{color};color:white;padding:8px 14px;border-radius:10px 10px 0 0;"
        f"font-weight:600;font-size:13px;display:flex;justify-content:space-between;'>"
        f"<span>🛠 EdgeOps Alert — {st.session_state.equipment_id}</span>"
        f"<span style='background:rgba(255,255,255,0.25);padding:1px 8px;border-radius:9999px;font-size:11px;'>"
        f"{risk.risk_level}</span></div>"
        f"<div style='padding:12px 14px;font-size:13px;color:#0f172a;'>"
        f"<div style='margin-bottom:6px;'>{body_text}</div>"
        f"<div style='color:#475569;font-size:12px;margin-top:8px;'>"
        f"作業指示書の発行には管理者承認が必要です。Work Order タブで承認/却下できます。"
        f"</div></div>"
        f"<div style='padding:8px 14px;border-top:1px solid #e2e8f0;background:#f1f5f9;border-radius:0 0 10px 10px;"
        f"font-size:12px;color:#475569;'>"
        f"このプレビューは Adaptive Card 形式での Teams 通知を想定したデモ表示です。"
        f"Power Automate / Logic Apps と組み合わせて実通知に拡張可能です。"
        f"</div></div>",
        unsafe_allow_html=True,
    )


# ───────────────────────────────────────────────────────────────────────────
# Data Upload tab
# ───────────────────────────────────────────────────────────────────────────

_RAW_CHANNEL_LABELS = {
    "timestamp":   "時刻 / timestamp（任意・サンプリングレート推定に使用）",
    "vibration_z": "主振動軸 vibration_z ★必須（FFT/軸受帯域/RMS）",
    "vibration_x": "振動X（任意）",
    "vibration_y": "振動Y（任意）",
    "sound_level": "音響レベル dB（任意）",
    "temperature": "温度 ℃（任意）",
    "current":     "電流 A（任意）",
}


def _raw_data_ingest_section() -> None:
    """Ingest an arbitrary real-world sensor CSV: upload → auto-map columns →
    confirm sampling rate & equipment → preview rule-based result → apply.

    This is the path that makes the agent run on *real data*, not just the
    canonical demo presets — non-canonical column names and arbitrary sample
    rates are handled explicitly instead of silently degrading to zeros."""
    with st.expander("🛠 生データ取り込み（実データ・列マッピング）", expanded=True):
        st.caption(
            "任意のセンサーCSVを取り込めます。列名やサンプリングレートが異なっても、"
            "各チャンネルへの割り当てを指定すれば、デモと同じ8エージェント解析にかけられます。"
        )
        raw_up = st.file_uploader("raw-sensor.csv（列名は任意）", type=["csv"], key="raw_csv")
        if raw_up is None:
            st.caption(
                "例: `time_s, accel_z, mic_dB, temp_C, motor_amps` のような実機ログでもOK。"
                " アップロードすると列の自動マッピング候補を提示します。"
            )
            return

        try:
            raw_df = pd.read_csv(raw_up)
        except Exception as exc:
            st.error(f"CSV 読み込みに失敗しました: {exc}")
            return
        if raw_df.empty:
            st.warning("空の CSV です。")
            return

        st.markdown(f"**検出: {len(raw_df.columns)} 列 × {len(raw_df)} 行**")
        st.dataframe(raw_df.head(5), use_container_width=True, hide_index=True)

        # ── Column mapping (auto-detected, user-editable) ──
        options = ["（なし）"] + [str(c) for c in raw_df.columns]
        auto = raw_ingest.auto_detect_mapping([str(c) for c in raw_df.columns])
        st.markdown("##### 列の割り当て")
        mapping: dict[str, str | None] = {}
        mcols = st.columns(2)
        for i, channel in enumerate(raw_ingest.CANONICAL_CHANNELS):
            default = auto.get(channel)
            idx = options.index(default) if default in options else 0
            sel = mcols[i % 2].selectbox(
                _RAW_CHANNEL_LABELS[channel], options, index=idx, key=f"rawmap_{channel}",
            )
            mapping[channel] = None if sel == "（なし）" else sel

        # ── Sampling rate (auto-inferred from timestamp when possible) ──
        inferred = raw_ingest.infer_sample_rate(raw_df, mapping.get("timestamp"))
        default_fs = float(inferred or st.session_state.get("sample_rate_hz") or signal_analysis.SAMPLE_RATE_HZ)
        fcol, ecol = st.columns(2)
        fs = fcol.number_input(
            "サンプリングレート (Hz)", min_value=1.0, max_value=200000.0,
            value=default_fs, step=1.0, key="raw_fs",
            help="FFT・軸受帯域の周波数解析に使用。timestamp があれば自動推定します。",
        )
        if inferred:
            fcol.caption(f"timestamp から **{inferred} Hz** を推定（必要なら上書き可）。")
        else:
            fcol.caption("timestamp 未割当のため手動指定してください。")

        eq_ids = [s.id for s in equipment_catalog.list_equipment()]
        eq_idx = eq_ids.index(st.session_state.equipment_id) if st.session_state.equipment_id in eq_ids else 0
        eq_sel = ecol.selectbox(
            "リスク判定に使う設備（しきい値）", eq_ids, index=eq_idx, key="raw_equipment",
            help="設備種別ごとにリスク閾値が異なります。データの設備に近いものを選んでください。",
        )

        # ── Live preview of what the rule engine would say ──
        res = raw_ingest.apply_mapping(raw_df, mapping, float(fs))
        present = "、".join(res.present_channels) or "（なし）"
        st.caption(f"割当済みチャンネル: {present} ／ {res.sample_count} サンプル ／ 約 {res.duration_seconds:.2f} 秒")
        for w in res.warnings:
            st.warning(w)

        if res.has_primary_vibration:
            prev_feat = signal_analysis.analyze(res.canonical_df, fs=float(fs))
            prev_risk = risk_engine.assess(prev_feat, equipment_id=eq_sel)
            st.markdown("##### プレビュー（ルールベース判定）")
            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Vib RMS [G]", f"{prev_feat.vibration_rms:.3f}")
            p2.metric("Sound Max [dB]", f"{prev_feat.sound_max_db:.1f}")
            p3.metric("Temp Max [℃]", f"{prev_feat.temperature_max_c:.1f}")
            p4.metric("100-300Hz比", f"{prev_feat.bearing_band_energy_ratio:.2f}")
            color = RISK_COLORS.get(prev_risk.risk_level, "#94a3b8")
            st.markdown(
                f"<span style='background:{color};color:#fff;padding:3px 12px;border-radius:9999px;"
                f"font-size:13px;font-weight:700;'>{prev_risk.risk_level}</span>"
                f"&nbsp; 主要懸念: {prev_risk.primary_concern}",
                unsafe_allow_html=True,
            )

        if st.button("▶ この生データで解析する", type="primary", key="apply_raw", use_container_width=True):
            if not res.has_primary_vibration:
                st.error("主振動軸 vibration_z を割り当ててください（必須）。")
            else:
                st.session_state.sensor_df = res.canonical_df
                st.session_state.sample_rate_hz = float(fs)
                st.session_state.equipment_id = eq_sel
                st.session_state.active_preset_key = f"raw:{raw_up.name}"
                st.session_state.pipeline = None
                st.session_state.approval_status = "未承認"
                _recompute_features()
                st.success(
                    f"生データを取り込みました（{res.sample_count} 行 / {fs:.0f} Hz / {eq_sel}）。"
                    " Command Center で結果を確認し、左サイドバーの **▶ Run Agents** で 8 エージェント解析を実行できます。"
                )
                st.rerun()


def _tab_data_upload() -> None:
    st.markdown("## Data Upload")
    st.caption("実データ（生CSV）も、デモデータも投入できます。デモデータは左サイドバーから一発でロード可能です。")

    _raw_data_ingest_section()

    with st.expander("点検写真アップロード（オプション）", expanded=False):
        up_img = st.file_uploader(
            "pump.jpg / png", type=["jpg", "jpeg", "png"], key="upload_img"
        )
        if up_img is not None:
            tmp = utils.PROJECT_ROOT / "_uploaded_image.jpg"
            tmp.write_bytes(up_img.getvalue())
            st.session_state.image_path = tmp
            st.image(up_img, caption="アップロードされた画像", width=380)
            # Also push to Blob storage so the demo shows the persistence path.
            try:
                blob_res = blob_store.upload_bytes(
                    up_img.getvalue(),
                    blob_name=f"images/{st.session_state.equipment_id}/{up_img.name}",
                    content_type=up_img.type or "image/jpeg",
                )
                if blob_res.backend == "azure":
                    st.caption(f"📦 Blob 保存: `{blob_res.container}/{blob_res.blob_name}`")
                else:
                    st.caption(f"📂 ローカル保存: `{blob_res.url}`（AZURE_STORAGE_CONNECTION_STRING 未設定）")
            except Exception as exc:
                st.warning(f"Blob 保存に失敗しました: {exc}")

    with st.expander("追加アングル / 近接写真（Vision 強化）", expanded=False):
        st.caption(
            "メインの点検写真に加え、近接や別アングルを最大 4 枚まで指定できます。"
            " Vision Agent はこれらを `IMAGE 2..N` として参照し、領域別所見を厚くします。"
        )
        extras = st.file_uploader(
            "detail-shots.jpg / png（複数選択可）",
            type=["jpg", "jpeg", "png"],
            accept_multiple_files=True,
            key="upload_extra_imgs",
        )
        if extras:
            saved: list[Path] = []
            for i, up in enumerate(extras[:4]):
                p = utils.PROJECT_ROOT / f"_extra_image_{i}.jpg"
                p.write_bytes(up.getvalue())
                saved.append(p)
            st.session_state.extra_image_paths = saved
            cols = st.columns(min(4, len(extras)))
            for col, up in zip(cols, extras[:4]):
                col.image(up, width=160, caption=up.name)
        if st.session_state.extra_image_paths:
            if st.button("追加アングルをクリア", key="clear_extras"):
                for p in st.session_state.extra_image_paths:
                    try:
                        Path(p).unlink(missing_ok=True)
                    except Exception:
                        pass
                st.session_state.extra_image_paths = []
                st.rerun()

        st.markdown("---")
        st.caption(
            "**参照（正常時）写真**: アップロードすると Vision Agent が `comparison_to_normal` で"
            " 差分を算出します。未指定時は同設備のカタログ正常画像を自動で使います。"
        )
        ref_up = st.file_uploader(
            "reference_normal.jpg / png", type=["jpg", "jpeg", "png"], key="upload_ref_img"
        )
        if ref_up is not None:
            p = utils.PROJECT_ROOT / "_reference_image.jpg"
            p.write_bytes(ref_up.getvalue())
            st.session_state.reference_image_override = p
            st.image(ref_up, width=200, caption="参照（正常時）")
        if st.session_state.reference_image_override:
            if st.button("参照写真をクリア", key="clear_ref"):
                try:
                    Path(st.session_state.reference_image_override).unlink(missing_ok=True)
                except Exception:
                    pass
                st.session_state.reference_image_override = None
                st.rerun()

    with st.expander("マニュアル PDF アップロード（オプション）", expanded=False):
        st.caption(
            "現場の保全マニュアル PDF を投入します。チャンクは (1) Blob 保存、"
            " (2) RAG への登録、(3) Azure AI Search が設定済みなら同じインデックスにも投入されます。"
        )
        up_pdf = st.file_uploader("manual.pdf", type=["pdf"], key="upload_pdf")
        if up_pdf is not None:
            pdf_bytes = up_pdf.getvalue()
            with st.spinner(f"{up_pdf.name} を解析中…"):
                blob_res = blob_store.upload_bytes(
                    pdf_bytes,
                    blob_name=f"manuals/{up_pdf.name}",
                    content_type="application/pdf",
                )
                rag_res = rag.ingest_pdf_bytes(pdf_bytes, source_name=up_pdf.name)
            st.success(
                f"取り込み完了: chunks={rag_res['chunks_extracted']} / "
                f"local={rag_res['added_to_local']} / "
                f"Azure={rag_res['azure_status']} / "
                f"Blob={blob_res.backend}"
            )
            st.caption(f"現在の RAG バックエンド: **{rag.active_backend()}**")

    with st.expander("Spresense ストリーム取り込み（オプション）", expanded=False):
        st.caption(
            "Spresense（または互換センサー）から Event Hubs に送られた最新のサンプルを取得し、"
            "そのままセンサーデータとして読み込みます。Event Hubs が未設定のときは "
            "`_spresense_stream.jsonl`（シミュレータが書き出すローカルファイル）から読み込みます。"
        )
        st.caption(f"現在のソース: **{iot_ingest.active_source()}**")
        c1, c2 = st.columns([1, 1])
        if c1.button("📡 直近の Spresense サンプルを取得", key="spresense_fetch"):
            with st.spinner("ストリーム取得中…"):
                fetch = iot_ingest.fetch_recent(equipment_id=st.session_state.equipment_id)
            if fetch.df.empty:
                st.warning(
                    "サンプルが取得できませんでした。"
                    "`python data/spresense_simulator.py --intensity warning` でローカルに流し込めます。"
                )
            else:
                st.session_state.sensor_df = fetch.df
                st.session_state.active_preset_key = f"spresense:{fetch.source}"
                _recompute_features()
                st.success(
                    f"{fetch.record_count} 件取得しました（source={fetch.source}）。"
                    "Run Agents で解析してください。"
                )
        if c2.button("🧹 ローカルストリームをリセット", key="spresense_reset"):
            removed = iot_ingest.reset_local_stream()
            st.info(f"ローカル JSONL をクリアしました（{removed} 行）")
        st.code(
            "# ローカルでシミュレータを動かす:\n"
            "python data/spresense_simulator.py --equipment-id Pump-03 --intensity critical --duration 5",
            language="powershell",
        )

    st.markdown("### 点検メモ")
    memo = st.text_area(
        "現場の点検メモ",
        value=st.session_state.inspection_memo,
        height=120,
        label_visibility="collapsed",
        placeholder="例: 軸受周辺で異音あり。前回点検時と比べて運転音が大きい。",
    )
    if memo != st.session_state.inspection_memo:
        st.session_state.inspection_memo = memo

    df = st.session_state.sensor_df
    if df is not None:
        st.markdown("### 取り込み済みセンサーデータ（先頭10行）")
        st.dataframe(df.head(10), use_container_width=True)

    st.markdown("### 故障履歴 & 部品在庫")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Failure History**")
        try:
            hist = pd.read_csv(utils.DATA_DIR / "failure_history.csv")
            st.dataframe(hist, use_container_width=True, height=240)
        except Exception as exc:
            st.warning(f"履歴ファイルを読めませんでした: {exc}")
    with c2:
        st.markdown("**Parts Inventory**")
        try:
            inv = pd.read_csv(utils.DATA_DIR / "parts_inventory.csv")
            st.dataframe(inv, use_container_width=True, height=240)
        except Exception as exc:
            st.warning(f"在庫ファイルを読めませんでした: {exc}")


# ───────────────────────────────────────────────────────────────────────────
# Signal Analysis tab
# ───────────────────────────────────────────────────────────────────────────

def _tab_signal_analysis() -> None:
    st.markdown("## Signal Analysis")
    st.caption("時系列波形 / FFT / 統計量。点検データを定量的に解釈します。")

    df: pd.DataFrame | None = st.session_state.sensor_df
    features: SignalFeatures | None = st.session_state.features
    risk: RiskAssessment | None = st.session_state.risk

    if df is None or features is None:
        _render_empty_state(
            icon="📈",
            title="センサーデータが未投入です",
            hint="左サイドバーの **設備** と **シナリオ強度** を選ぶと、振動・音響・温度・電流の時系列と FFT スペクトラムが表示されます。",
        )
        return

    # Feature metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Vibration RMS [G]", f"{features.vibration_rms:.3f}")
    c2.metric("Vibration Peak [G]", f"{features.vibration_peak:.3f}")
    c3.metric("Sound Max [dB]", f"{features.sound_max_db:.1f}")
    c4.metric("Temp Max [℃]", f"{features.temperature_max_c:.1f}")
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Current Mean [A]", f"{features.current_mean_a:.2f}")
    c6.metric("Temp Trend [℃/s]", f"{features.temperature_trend_c_per_s:+.3f}")
    c7.metric("Current Trend [A/s]", f"{features.current_trend_a_per_s:+.4f}")
    c8.metric("100-300Hz Energy Ratio", f"{features.bearing_band_energy_ratio:.2f}")

    # Time-series & FFT
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=("振動 (z軸)", "音響レベル [dB]", "温度 [℃]", "FFT スペクトラム (z軸)"),
        specs=[[{"type": "xy"}, {"type": "xy"}], [{"type": "xy"}, {"type": "xy"}]],
        horizontal_spacing=0.10, vertical_spacing=0.16,
    )
    fs = st.session_state.get("sample_rate_hz", signal_analysis.SAMPLE_RATE_HZ)
    # Raw-data imports may omit channels — only plot the ones actually present,
    # and synthesise a time axis from fs when there's no timestamp column.
    t = df["timestamp"].to_numpy() if "timestamp" in df.columns else np.arange(len(df)) / (fs or 1.0)
    if "vibration_z" in df.columns:
        fig.add_trace(go.Scatter(x=t, y=df["vibration_z"], mode="lines", line=dict(color="#3b82f6", width=1), name="vib_z"), row=1, col=1)
    if "sound_level" in df.columns:
        fig.add_trace(go.Scatter(x=t, y=df["sound_level"], mode="lines", line=dict(color="#8b5cf6", width=1), name="sound"), row=1, col=2)
    if "temperature" in df.columns:
        fig.add_trace(go.Scatter(x=t, y=df["temperature"], mode="lines", line=dict(color="#ef4444", width=2), name="temp"), row=2, col=1)

    freqs, spectrum = spectrum_for_plot(df, axis="vibration_z", fs=fs)
    if freqs.size and spectrum.size:
        # plot up to 500Hz for readability
        mask = freqs <= 500
        fig.add_trace(
            go.Scatter(x=freqs[mask], y=spectrum[mask], mode="lines", line=dict(color="#0ea5e9", width=1), name="FFT"),
            row=2, col=2,
        )
        # annotate top peaks
        for p in features.fft_peaks[:3]:
            if p.frequency_hz <= 500:
                fig.add_annotation(
                    x=p.frequency_hz, y=p.amplitude,
                    text=f"{p.frequency_hz:.0f}Hz",
                    showarrow=True, arrowhead=2, ax=0, ay=-30,
                    row=2, col=2,
                )

    fig.update_xaxes(title_text="time [s]", row=1, col=1)
    fig.update_xaxes(title_text="time [s]", row=1, col=2)
    fig.update_xaxes(title_text="time [s]", row=2, col=1)
    fig.update_xaxes(title_text="frequency [Hz]", row=2, col=2)
    fig.update_layout(height=540, showlegend=False, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)

    # Rule-based findings table
    if risk is not None:
        st.markdown("### ルールベース判定（マニュアル根拠）")
        rows = []
        for f in risk.findings:
            rows.append({
                "Indicator": f.indicator,
                "Value": round(f.value, 4),
                "Threshold": round(f.threshold, 4),
                "Level": f.level,
                "Note": f.note,
            })
        rdf = pd.DataFrame(rows)
        st.dataframe(rdf, use_container_width=True, hide_index=True)


# ───────────────────────────────────────────────────────────────────────────
# Vision Inspection tab
# ───────────────────────────────────────────────────────────────────────────

def _flow_step_header(num: int, title: str, subtitle: str = "") -> None:
    sub = f"<div class='eo-flow-step-sub'>{subtitle}</div>" if subtitle else ""
    st.markdown(
        f"<div class='eo-flow-step'>"
        f"<div class='eo-flow-step-num'>{num}</div>"
        f"<div><div class='eo-flow-step-title'>{title}</div>{sub}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _tab_vision() -> None:
    st.markdown("## Vision Inspection")
    st.caption("点検写真をどう見て、どう判断したか。AI のフローを5ステップで可視化します。")

    pipeline = st.session_state.pipeline
    image_path: Path | None = st.session_state.image_path

    if image_path is None or not Path(image_path).exists():
        _render_empty_state(
            icon="🖼",
            title="画像が未投入です",
            hint="Data Upload タブから画像を投入するか、左サイドバーでデモ設備を選択してください。",
        )
        return

    # ── STEP 1 — Input
    _flow_step_header(1, "入力 (Input)",
                      "AI に渡した画像と点検メモ。後続ステップの判断は、この観測事実から始まります。")
    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown(
            "<div class='eo-trust-row'>"
            "<span class='eo-trust-tag eo-trust-observed'>観測事実</span>"
            "<span class='eo-flow-sublabel'>点検写真</span></div>",
            unsafe_allow_html=True,
        )
        st.image(Image.open(image_path), use_container_width=True, caption=str(Path(image_path).name))
    with c2:
        st.markdown(
            "<div class='eo-trust-row'>"
            "<span class='eo-trust-tag eo-trust-observed'>観測事実</span>"
            "<span class='eo-flow-sublabel'>点検メモ / 設備情報</span></div>",
            unsafe_allow_html=True,
        )
        memo = st.session_state.inspection_memo or "（メモなし）"
        st.markdown(
            f"<div class='eo-card' style='margin-bottom:6px;'>{memo}</div>",
            unsafe_allow_html=True,
        )
        try:
            spec = equipment_catalog.get(st.session_state.equipment_id)
            chips = (
                f"<span class='eo-chip'>{spec.kind_icon} {spec.id}</span>"
                f"<span class='eo-chip'>{spec.kind}</span>"
                f"<span class='eo-chip'>{spec.location}</span>"
            )
            st.markdown(f"<div class='eo-chip-row'>{chips}</div>", unsafe_allow_html=True)
        except KeyError:
            pass

    if pipeline is None:
        st.markdown("---")
        _render_empty_state(
            icon="🤖",
            title="AI 所見はまだ生成されていません",
            hint="左サイドバーの **▶ Run Agents** を実行すると、領域分割→所見抽出→センサー整合→判断 の AI フローがここに表示されます。",
        )
        return

    v = pipeline.vision.output if isinstance(pipeline.vision.output, dict) else {}
    source = pipeline.vision.source
    regions = v.get("regions") or []
    evidence = v.get("evidence_images") or {}

    # ── STEP 2 — Region detection (where the AI looked)
    _flow_step_header(2, "領域分割 (AIが見た場所)",
                      "AI が点検対象として注目した領域。色は重大度、ラベルは region_id と確信度。")
    # Severity distribution chips
    sev_counts: dict[str, int] = {}
    for r in regions:
        if isinstance(r, dict):
            sev = str(r.get("severity", "minor")).lower()
            sev_counts[sev] = sev_counts.get(sev, 0) + 1
    chip_palette = {
        "normal":   ("#dcfce7", "#166534"),
        "minor":    ("#fef3c7", "#92400e"),
        "moderate": ("#fed7aa", "#9a3412"),
        "severe":   ("#fee2e2", "#b91c1c"),
    }
    _default_chip = ("#f1f5f9", "#475569")
    chip_html_parts: list[str] = []
    for s, n in sev_counts.items():
        bg, fg = chip_palette.get(s, _default_chip)
        chip_html_parts.append(
            f"<span class='eo-chip' style='background:{bg};color:{fg};'>{s} × {n}</span>"
        )
    sev_chips = "".join(chip_html_parts)
    st.markdown(
        f"<div class='eo-trust-row'>"
        f"<span class='eo-trust-tag eo-trust-ai'>AI推論</span>"
        f"<span class='eo-flow-sublabel'>検出領域 {len(regions)} 件</span></div>"
        f"<div class='eo-chip-row' style='margin-bottom:8px;'>{sev_chips}</div>",
        unsafe_allow_html=True,
    )
    if evidence.get("overlay"):
        st.image(evidence["overlay"], use_container_width=True,
                 caption="重大度別に色分けされた BBOX オーバーレイ（赤=severe / 橙=moderate / 黄=minor / 緑=normal）")
    else:
        st.caption("（BBOX オーバーレイは生成されていません）")

    # ── STEP 3 — Per-region findings (what the AI saw at each region)
    _flow_step_header(3, "領域別所見 (各場所で何を見たか)",
                      "AI が各領域でどんな視覚的特徴を観察したか。クロップ拡大画像とともに表示。")
    st.markdown(
        "<div class='eo-trust-row'>"
        "<span class='eo-trust-tag eo-trust-ai'>AI推論</span>"
        "<span class='eo-flow-sublabel'>領域 × 観察事項 × 推奨アクション</span></div>",
        unsafe_allow_html=True,
    )
    crops: dict[str, str] = evidence.get("crops") or {}
    if regions:
        _SEV_TAG = {
            "normal":   ("#dcfce7", "#166534"),
            "minor":    ("#fef3c7", "#92400e"),
            "moderate": ("#fed7aa", "#9a3412"),
            "severe":   ("#fee2e2", "#b91c1c"),
        }
        for r in regions:
            if not isinstance(r, dict):
                continue
            rid = str(r.get("region_id", "—"))
            sev = str(r.get("severity", "—")).lower()
            obs = r.get("observation", "")
            conf = r.get("confidence_score", 0)
            action = r.get("recommended_action", "")
            evidence_lines = r.get("evidence") or []
            sev_bg, sev_fg = _SEV_TAG.get(sev, ("#f1f5f9", "#475569"))

            cc = st.columns([1, 3])
            with cc[0]:
                if rid in crops:
                    st.image(crops[rid], use_container_width=True)
                else:
                    st.markdown(
                        "<div class='eo-empty-state' style='padding:20px 8px;'>"
                        "<div style='font-size:24px;opacity:0.5;'>🔍</div>"
                        "<div style='font-size:11px;'>crop なし</div></div>",
                        unsafe_allow_html=True,
                    )
            with cc[1]:
                evidence_html = "".join(f"<li>{e}</li>" for e in evidence_lines)
                st.markdown(
                    f"<div class='eo-card' style='margin-bottom:6px;'>"
                    f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px;'>"
                    f"<code style='background:#f1f5f9;padding:2px 8px;border-radius:6px;font-size:12px;'>{rid}</code>"
                    f"<span class='eo-chip' style='background:{sev_bg};color:{sev_fg};'>severity: {sev}</span>"
                    f"<span style='margin-left:auto;font-size:11px;color:var(--eo-muted);'>確信度 {conf}%</span>"
                    f"</div>"
                    f"<div style='font-size:13.5px;line-height:1.55;color:var(--eo-text);margin-bottom:6px;'>{obs}</div>"
                    + (f"<details style='margin-bottom:6px;'><summary style='font-size:11px;color:var(--eo-muted);cursor:pointer;'>視覚的根拠 ({len(evidence_lines)} 件)</summary>"
                       f"<ul style='margin:6px 0 0;padding-left:18px;font-size:12px;color:#475569;'>{evidence_html}</ul></details>"
                       if evidence_lines else "")
                    + f"<div style='font-size:12.5px;color:#1d4ed8;'>→ {action}</div>"
                    "</div>",
                    unsafe_allow_html=True,
                )
    else:
        st.caption("領域が検出されませんでした。")

    # ── STEP 4 — Cross-check with sensor signals
    correlation = v.get("signal_correlation") or ""
    comparison = v.get("comparison_to_normal") or ""
    _flow_step_header(4, "センサーとの整合 (Cross-check)",
                      "視覚観察とセンサーデータが同じ仮説を支持しているかをチェック。AI 単独の判断にしないための監視。")
    st.markdown(
        "<div class='eo-trust-row'>"
        "<span class='eo-trust-tag eo-trust-observed'>観測事実</span>"
        "<span class='eo-trust-tag eo-trust-ai'>AI推論</span>"
        "<span class='eo-flow-sublabel'>視覚 × センサー × 正常時比較</span></div>",
        unsafe_allow_html=True,
    )
    cor_col, cmp_col = st.columns(2)
    cor_col.markdown(
        f"<div class='eo-callout eo-callout-info'>"
        f"<div class='eo-callout-title'>センサーとの整合</div>"
        f"{correlation or '（センサー整合の所見なし）'}</div>",
        unsafe_allow_html=True,
    )
    cmp_col.markdown(
        f"<div class='eo-callout' style='border-left-color:#7c3aed;background:#faf5ff;'>"
        f"<div class='eo-callout-title'>正常時との比較</div>"
        f"{comparison or '（参照画像未指定 — Data Upload タブから正常時画像を投入すると差分が出ます）'}</div>",
        unsafe_allow_html=True,
    )

    # ── STEP 5 — Decision + human gate
    overview = v.get("overview") or ""
    shots = v.get("recommended_additional_shots") or []
    overall_conf = v.get("overall_confidence_score", 0)
    human_required = bool(v.get("human_confirmation_required"))

    _flow_step_header(5, "結論と次のアクション (Decision)",
                      "AI の総合所見と確信度。確定判断は人間承認が前提。")
    st.markdown(
        "<div class='eo-trust-row'>"
        "<span class='eo-trust-tag eo-trust-ai'>AI推論</span>"
        + ("<span class='eo-trust-tag eo-trust-human'>人間確認</span>" if human_required else "")
        + f"<span class='eo-flow-sublabel'>総合確信度 {overall_conf}%</span></div>",
        unsafe_allow_html=True,
    )
    overview_html = overview or "（総合所見が生成されませんでした）"
    st.markdown(
        f"<div class='eo-card'>"
        f"<div style='font-size:14px;line-height:1.6;color:var(--eo-text);'>{overview_html}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if shots:
        shots_html = "".join(f"<li>{s}</li>" for s in shots)
        st.markdown(
            f"<div class='eo-callout eo-callout-info'>"
            f"<div class='eo-callout-title'>📸 追加撮影の提案</div>"
            f"<ul style='margin:4px 0 0;padding-left:18px;'>{shots_html}</ul></div>",
            unsafe_allow_html=True,
        )
    if human_required:
        st.markdown(
            "<div class='eo-callout eo-callout-warning'>"
            "<div class='eo-callout-title'>"
            "<span class='eo-trust-tag eo-trust-human'>人間確認</span>"
            "確定診断には現場での目視確認が必要"
            "</div>"
            "AI 単独の判断では設備停止 / 部品交換は実施しません。Work Order タブで管理者承認後に作業実施します。"
            "</div>",
            unsafe_allow_html=True,
        )

    # Enhanced view + Markdown source — both pushed into expanders to keep the
    # flow itself clean.
    if evidence.get("enhanced"):
        with st.expander("🔬 自動コントラスト強調ビュー（微小変化を強調）", expanded=False):
            st.image(evidence["enhanced"], use_container_width=True)
    st.caption(f"source: {source}  ／  confidence: {v.get('confidence', '?')}")


# ───────────────────────────────────────────────────────────────────────────
# Agent Reasoning tab
# ───────────────────────────────────────────────────────────────────────────

def _tab_agent_reasoning() -> None:
    st.markdown("## Agent Reasoning")
    st.caption("8つの Agent が順番に判断材料を組み立て、最後に人間が承認する流れを可視化します。")

    pipeline = st.session_state.pipeline
    if pipeline is None:
        _render_empty_state(
            icon="🤖",
            title="Agent はまだ実行されていません",
            hint="左サイドバーの **▶ Run Agents** を押すと、8つの Agent が順に判断材料を組み立てる過程が、ここにタイムラインで表示されます。",
        )
        if st.session_state.agent_log:
            with st.expander("直近の Agent 実行ログ", expanded=False):
                for line in st.session_state.agent_log:
                    st.text(line)
        return

    steps: list[tuple[str, str, str, str, Any, Any]] = []
    if pipeline.intake is not None:
        steps.append(("Intake Agent", "観測事実", "eo-trust-observed",
                      "入力データの品質・欠損・期間を点検",
                      pipeline.intake, _render_intake_block))
    steps += [
        ("Signal Insight Agent",     "観測事実",     "eo-trust-observed", "振動・音響・温度から異常傾向を抽出",     pipeline.signal,     _render_signal_block),
        ("Vision Inspection Agent",  "観測事実",     "eo-trust-observed", "設備画像から外観異常候補を抽出",         pipeline.vision,     _render_vision_block),
        ("Manual RAG Agent",         "マニュアル根拠", "eo-trust-manual",  "点検基準・マニュアルから根拠を検索",     pipeline.manual_rag, _render_manual_block),
        ("Root Cause Agent",         "AI推論",        "eo-trust-ai",       "原因仮説と追加確認項目を生成",           pipeline.root_cause, _render_root_cause_block),
        ("Action Planning Agent",    "AI推論",        "eo-trust-ai",       "作業指示と対応期限を生成",               pipeline.action_plan, _render_action_block),
        ("What-if Simulator",        "AI推論",        "eo-trust-ai",       "状態が悪化した場合の波及を試算",         pipeline.whatif,     _render_whatif_block),
    ]
    if pipeline.governance is not None:
        steps.append(("Governance Agent", "人間確認", "eo-trust-human",
                      "不確実性を集約し、管理者承認ゲートを準備",
                      pipeline.governance, _render_governance_block))
    for i, (name, trust_label, trust_cls, desc, result, renderer) in enumerate(steps, start=1):
        if result.error:
            status_class, status_label = "error", "FALLBACK"
        else:
            status_class, status_label = "done", "DONE"
        preview = _agent_preview(name, result.output if isinstance(result.output, dict) else {})
        preview = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", preview)
        thumbs_html = _agent_thumbs(name, result.output if isinstance(result.output, dict) else {})
        st.markdown(
            f"""
<div class='eo-agent-step {status_class}'>
  <div class='eo-agent-step-head'>
    <span>{i}. {name}</span>
    <span class='eo-agent-status {status_class}'>{status_label} · {result.source} · {result.elapsed_ms} ms</span>
  </div>
  <div class='eo-agent-step-desc'>{desc}</div>
  <div class='eo-agent-step-preview'>
    <span class='eo-trust-tag {trust_cls}'>{trust_label}</span>{preview}
  </div>
  {thumbs_html}
</div>
            """,
            unsafe_allow_html=True,
        )
        with st.expander("詳細を表示", expanded=False):
            if result.error:
                st.warning(f"Azure OpenAI 呼び出しに失敗しモックにフォールバックしました: {result.error}")
            renderer(result.output if isinstance(result.output, dict) else {"raw": result.output})

    # Step 7 — human approval (status driven by the Work Order tab approval widget)
    approval_status = st.session_state.get("approval_status", "未承認")
    if approval_status == "承認済":
        ap_class, ap_label = "done", "APPROVED"
    elif approval_status in ("却下", "修正依頼"):
        ap_class, ap_label = "error", approval_status.upper()
    else:
        ap_class, ap_label = "idle", "AWAITING"
    st.markdown(
        f"""
<div class='eo-agent-step {ap_class}'>
  <div class='eo-agent-step-head'>
    <span>7. Human Approval</span>
    <span class='eo-agent-status {ap_class}'>{ap_label}</span>
  </div>
  <div class='eo-agent-step-desc'>管理者が Work Order / Report を承認すると作業実施可能になります。</div>
</div>
        """,
        unsafe_allow_html=True,
    )

    _followup_qa_panel()


_QA_SUGGESTIONS = ["なぜこの判断？原因は？", "いつまでに対応すべき？", "放置するとどうなる？", "コストは？回避額は？"]


def _followup_qa_panel() -> None:
    """Conversational follow-up: ask the completed analysis a free-text
    question and get a grounded answer (Azure OpenAI live, deterministic
    grounded mock offline). Makes the pipeline interrogable, not one-shot."""
    pipeline = st.session_state.pipeline
    if pipeline is None:
        return

    st.markdown("### 💬 エージェントに質問（解析結果に基づく）")
    st.caption(
        "生成された解析結果について追質問できます。回答は上の8エージェントの出力だけを根拠にし、"
        "断定を避け、根拠の Agent を明示します。"
    )

    # One-click suggested questions.
    scols = st.columns(len(_QA_SUGGESTIONS))
    asked: str | None = None
    for col, sug in zip(scols, _QA_SUGGESTIONS):
        if col.button(sug, key=f"qa_sug_{sug}", use_container_width=True):
            asked = sug

    typed = st.chat_input("質問を入力（例: 軸受摩耗と判断した根拠は？）")
    if typed:
        asked = typed

    if asked:
        with st.spinner("解析結果を参照して回答中…"):
            ans = agents.run_followup_qa(asked, pipeline)
        st.session_state.qa_history.append({"q": asked, "a": ans})
        st.rerun()

    # Render history (most recent first), with grounding + confidence badges.
    if st.session_state.qa_history:
        if st.button("会話をクリア", key="qa_clear"):
            st.session_state.qa_history = []
            st.rerun()
        conf_color = {"high": "#16a34a", "medium": "#d97706", "low": "#dc2626"}
        for turn in reversed(st.session_state.qa_history):
            q, a = turn["q"], turn["a"]
            with st.chat_message("user"):
                st.markdown(q)
            with st.chat_message("assistant"):
                st.markdown(a.get("answer", ""))
                conf = str(a.get("confidence", "")).lower()
                chips = "".join(
                    f"<span style='background:#eef2ff;color:#3730a3;padding:1px 8px;border-radius:9999px;"
                    f"font-size:11px;margin-right:4px;'>{g}</span>" for g in (a.get("grounded_in") or [])
                )
                badge = (
                    f"<span style='background:{conf_color.get(conf, '#64748b')};color:#fff;padding:1px 8px;"
                    f"border-radius:9999px;font-size:11px;font-weight:700;'>確信度 {conf or '—'}</span>"
                )
                human = (
                    "<span style='color:#b45309;font-size:11px;margin-left:6px;'>⚠ 要人間確認</span>"
                    if a.get("human_confirmation_required") else ""
                )
                st.markdown(
                    f"<div style='margin-top:4px;'>{badge}{human}<div style='margin-top:4px;'>根拠: {chips or '—'}</div></div>",
                    unsafe_allow_html=True,
                )


def _agent_preview(name: str, out: dict[str, Any]) -> str:
    """Extract a 1-line conclusion preview from an agent's structured output
    so the Agent Reasoning timeline shows what the agent actually concluded
    without forcing the user to expand the details."""
    if not isinstance(out, dict) or not out:
        return "（出力なし）"
    if name == "Intake Agent":
        avail = out.get("available_sources") or []
        miss = out.get("missing_sources") or []
        quality = out.get("data_quality", "—")
        return f"データ品質 **{quality}** ・ 入力 {len(avail)} / 不足 {len(miss)}"
    if name == "Governance Agent":
        conf = out.get("overall_confidence", "—")
        cks = out.get("approval_checkpoints") or []
        first = cks[0] if cks else "—"
        return f"信頼度 **{conf}** ・ 承認確認 {len(cks)} 件 ・ 初手: {first}"
    if name == "Signal Insight Agent":
        return out.get("summary") or "（要約なし）"
    if name == "Vision Inspection Agent":
        ov = out.get("overview") or ""
        regions = out.get("regions") or []
        sev_counts: dict[str, int] = {}
        for r in regions:
            if isinstance(r, dict):
                s = str(r.get("severity", "minor")).lower()
                sev_counts[s] = sev_counts.get(s, 0) + 1
        sev_str = " / ".join(f"{k}:{v}" for k, v in sev_counts.items()) or "—"
        return (ov or f"領域 {len(regions)} 件を検出") + f"  ▸  検出: {sev_str}"
    if name == "Manual RAG Agent":
        rules = out.get("applicable_rules") or []
        if rules and isinstance(rules[0], dict):
            first = rules[0]
            return f"{first.get('rule', '—')} → **{first.get('judgement', '—')}**"
        sections = out.get("retrieved_sections") or []
        if sections:
            return f"マニュアル {len(sections)} 件ヒット"
        return "（該当ルールなし）"
    if name == "Root Cause Agent":
        hyps = out.get("root_cause_hypotheses") or []
        if hyps and isinstance(hyps[0], dict):
            h = hyps[0]
            return f"最有力仮説: **{h.get('cause', '—')}**（尤度 {h.get('likelihood', '?')}）"
        return out.get("abnormality_summary") or "（仮説なし）"
    if name == "Action Planning Agent":
        priority = out.get("priority", "—")
        deadline = out.get("deadline_hours", "—")
        steps = out.get("work_steps") or []
        first = _clean_step(steps[0]) if steps else "—"
        return f"優先度 **{priority}** ・ {deadline}h 以内 ・ 初手: {first}"
    if name == "What-if Simulator":
        scenarios = out.get("scenarios") or []
        recommended = next((s for s in scenarios if isinstance(s, dict) and s.get("recommended")), None)
        if recommended:
            return f"推奨シナリオ: **{recommended.get('name', '—')}**  ▸  {recommended.get('predicted_risk', '?')}"
        if scenarios:
            return f"{len(scenarios)} シナリオを試算"
        return "（シナリオなし）"
    return "—"


def _agent_thumbs(name: str, out: dict[str, Any]) -> str:
    """Render small inline thumbnails for the Vision Agent step so the
    timeline shows AT-A-GLANCE what the AI looked at."""
    if name != "Vision Inspection Agent" or not isinstance(out, dict):
        return ""
    evidence = out.get("evidence_images") or {}
    crops = evidence.get("crops") or {}
    if not crops:
        return ""
    imgs = "".join(f"<img src='{url}' alt='{rid}' title='{rid}'/>"
                   for rid, url in list(crops.items())[:6])
    return f"<div class='eo-agent-thumbs'>{imgs}</div>"


def _trust_header(label_class: str, label_text: str, title: str) -> None:
    st.markdown(
        f"<div style='margin-top:6px;margin-bottom:4px;'>"
        f"<span class='eo-trust-tag {label_class}'>{label_text}</span>"
        f"<strong>{title}</strong></div>",
        unsafe_allow_html=True,
    )


def _render_intake_block(out: dict[str, Any]) -> None:
    quality = out.get("data_quality", "—")
    color_map = {"good": "#16a34a", "acceptable": "#f59e0b", "degraded": "#dc2626"}
    badge_color = color_map.get(str(quality).lower(), "#64748b")
    eq = out.get("equipment_id", "—")
    n = out.get("sample_count", "—")
    sec = out.get("duration_seconds", "—")
    st.markdown(
        f"<div style='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px;'>"
        f"<span style='background:{badge_color};color:#fff;padding:2px 10px;border-radius:9999px;font-size:11px;'>"
        f"データ品質 {quality}</span>"
        f"<span class='eo-chip'>設備 {eq}</span>"
        f"<span class='eo-chip'>サンプル {n}</span>"
        f"<span class='eo-chip'>観測 {sec}s</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    _trust_header("eo-trust-observed", "観測事実", "利用可能な入力")
    for src in out.get("available_sources", []) or []:
        st.markdown(f"- ✅ {src}")
    if out.get("missing_sources"):
        _trust_header("eo-trust-uncert", "不確実性", "欠落している入力")
        for src in out["missing_sources"]:
            st.markdown(f"- ⚠ {src}")
    if out.get("anomalies_in_input"):
        _trust_header("eo-trust-uncert", "不確実性", "入力データの違和感")
        for a in out["anomalies_in_input"]:
            st.markdown(f"- {a}")
    if out.get("downstream_warnings"):
        _trust_header("eo-trust-uncert", "不確実性", "後段 Agent への注意")
        for w in out["downstream_warnings"]:
            st.markdown(f"- {w}")


def _render_governance_block(out: dict[str, Any]) -> None:
    conf = out.get("overall_confidence", "—")
    badge_map = {"high": "#16a34a", "medium": "#f59e0b", "low": "#dc2626"}
    color = badge_map.get(str(conf).lower(), "#64748b")
    auto_exec = out.get("auto_executable", False)
    fallback = out.get("fallback_used") or []
    st.markdown(
        f"<div style='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px;'>"
        f"<span style='background:{color};color:#fff;padding:2px 10px;border-radius:9999px;font-size:11px;font-weight:700;'>"
        f"信頼度 {conf}</span>"
        f"<span class='eo-chip' style='background:{'#fee2e2' if not auto_exec else '#dcfce7'};color:{'#b91c1c' if not auto_exec else '#166534'};'>"
        f"{'自動実行不可（人間承認必須）' if not auto_exec else '自動実行可能（条件付）'}</span>"
        + (f"<span class='eo-chip' style='background:#fef3c7;color:#92400e;'>"
           f"モックフォールバック {len(fallback)}</span>" if fallback else "")
        + f"</div>",
        unsafe_allow_html=True,
    )
    if out.get("audit_notes"):
        _trust_header("eo-trust-human", "人間確認", "監査ログ要約")
        st.markdown(out["audit_notes"])
    if out.get("approval_checkpoints"):
        _trust_header("eo-trust-human", "人間確認", "管理者が承認前に確認する項目")
        for c in out["approval_checkpoints"]:
            st.markdown(f"- {c}")
    if out.get("uncertainty_drivers"):
        _trust_header("eo-trust-uncert", "不確実性", "判断のばらつき要因")
        for d in out["uncertainty_drivers"]:
            st.markdown(f"- {d}")
    if out.get("safety_constraints"):
        _trust_header("eo-trust-manual", "マニュアル根拠", "安全上の絶対遵守事項")
        for s in out["safety_constraints"]:
            st.markdown(f"- {s}")
    if fallback:
        _trust_header("eo-trust-uncert", "不確実性", "モックフォールバックした Agent")
        for f in fallback:
            st.markdown(f"- {f}")


def _render_signal_block(out: dict[str, Any]) -> None:
    st.markdown(f"**Summary**: {out.get('summary', '')}")
    _trust_header("eo-trust-observed", "観測事実", "Key Observations")
    for line in out.get("key_observations", []):
        st.markdown(f"- {line}")
    _trust_header("eo-trust-observed", "観測事実", "Frequency Findings")
    for line in out.get("frequency_findings", []):
        st.markdown(f"- {line}")
    if out.get("uncertainty_notes"):
        _trust_header("eo-trust-uncert", "不確実性", "Uncertainty Notes")
        for line in out["uncertainty_notes"]:
            st.markdown(f"- {line}")


def _render_vision_block(out: dict[str, Any]) -> None:
    overview = out.get("overview") or ""
    overall_conf = out.get("overall_confidence_score", 0)
    regions = out.get("regions") or []
    findings = out.get("visual_findings") or []
    evidence = out.get("evidence_images") or {}

    if overview:
        _trust_header("eo-trust-ai", "AI推論", "総合所見")
        st.markdown(overview)

    if evidence.get("overlay"):
        _trust_header("eo-trust-observed", "観測事実", "AI が見た場所 (BBOX オーバーレイ)")
        st.image(evidence["overlay"], use_container_width=True,
                 caption=f"検出領域 {len(regions)} 件 / 詳細は Vision Inspection タブ")

    if findings:
        _trust_header("eo-trust-ai", "AI推論", "視覚的所見")
        for line in findings:
            st.markdown(f"- {line}")

    correlation = out.get("signal_correlation") or ""
    if correlation:
        _trust_header("eo-trust-observed", "観測事実", "センサーとの整合")
        st.markdown(correlation)

    st.caption(f"総合確信度 {overall_conf}%  ／  text-confidence: `{out.get('confidence', '?')}`")


# ───────────────────────────────────────────────────────────────────────
# Vision Inspection — structured per-region renderer
# ───────────────────────────────────────────────────────────────────────

_SEVERITY_STYLES = {
    "normal":   {"label": "Normal",   "bg": "#dcfce7", "fg": "#166534"},
    "minor":    {"label": "Minor",    "bg": "#fef3c7", "fg": "#92400e"},
    "moderate": {"label": "Moderate", "bg": "#fed7aa", "fg": "#9a3412"},
    "severe":   {"label": "Severe",   "bg": "#fecaca", "fg": "#991b1b"},
}


def _severity_badge(sev: str) -> str:
    style = _SEVERITY_STYLES.get(str(sev).lower(), {"label": sev or "—", "bg": "#e2e8f0", "fg": "#475569"})
    return (
        f"<span style='display:inline-block;padding:2px 10px;border-radius:9999px;"
        f"background:{style['bg']};color:{style['fg']};font-size:11px;font-weight:600;'>"
        f"{style['label']}</span>"
    )


def _confidence_meter(score: int | float) -> str:
    try:
        s = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        s = 0
    color = "#22c55e" if s >= 80 else ("#f59e0b" if s >= 60 else "#ef4444")
    return (
        f"<div style='width:80px;height:6px;background:#e2e8f0;border-radius:3px;overflow:hidden;'>"
        f"<div style='width:{s}%;height:100%;background:{color};'></div></div>"
        f"<span style='font-size:10px;color:#64748b;'>{s}%</span>"
    )




def _render_manual_block(out: dict[str, Any]) -> None:
    _trust_header("eo-trust-manual", "マニュアル根拠", "該当ルール")
    for r in out.get("applicable_rules", []):
        if not isinstance(r, dict):
            continue
        st.markdown(f"- {r.get('rule', '')} — 観測値: `{r.get('current_value', '')}` → **{r.get('judgement', '')}**")
    _trust_header("eo-trust-manual", "マニュアル根拠", "推奨点検手順")
    for s in out.get("recommended_procedure_steps", []):
        st.markdown(f"- {s}")
    if out.get("retrieved_sections"):
        st.caption("検索ヒット: " + ", ".join(s.get("section", "?") for s in out["retrieved_sections"] if isinstance(s, dict)))


def _render_root_cause_block(out: dict[str, Any]) -> None:
    _trust_header("eo-trust-observed", "観測事実", "異常概要")
    st.markdown(out.get("abnormality_summary", ""))
    _trust_header("eo-trust-observed", "観測事実", "根拠")
    for line in out.get("evidence", []):
        st.markdown(f"- {line}")
    _trust_header("eo-trust-ai", "AI推論", "原因仮説")
    for h in out.get("root_cause_hypotheses", []):
        if not isinstance(h, dict):
            continue
        st.markdown(f"- **{h.get('cause', '')}**（尤度 `{h.get('likelihood', '?')}`） — {h.get('reason', '')}")
        for chk in h.get("additional_checks", []):
            st.markdown(f"   - 追加確認: {chk}")
    if out.get("uncertainty"):
        _trust_header("eo-trust-uncert", "不確実性", "判断のばらつき要因")
        st.markdown(out.get("uncertainty", ""))
    if out.get("human_confirmation_required"):
        st.markdown(
            "<div class='eo-callout eo-callout-warning'>"
            "<div class='eo-callout-title'><span class='eo-trust-tag eo-trust-human'>人間確認</span>最終判断は管理者承認後に実施</div>"
            "</div>",
            unsafe_allow_html=True,
        )


def _render_action_block(out: dict[str, Any]) -> None:
    st.markdown(f"**優先度**: `{out.get('priority', '')}` / **対応期限**: {out.get('deadline_hours', '?')} 時間以内")
    _trust_header("eo-trust-ai", "AI推論", "作業手順")
    for s in out.get("work_steps", []):
        st.markdown(f"- {s}")
    c1, c2 = st.columns(2)
    with c1:
        _trust_header("eo-trust-ai", "AI推論", "必要工具")
        for t in out.get("required_tools", []):
            st.markdown(f"- {t}")
    with c2:
        _trust_header("eo-trust-ai", "AI推論", "必要部品")
        for p in out.get("required_parts", []):
            st.markdown(f"- {p}")
    _trust_header("eo-trust-manual", "マニュアル根拠", "安全上の注意")
    for s in out.get("safety_notes", []):
        st.markdown(f"- {s}")
    if out.get("manager_approval_required"):
        st.markdown(
            "<div class='eo-callout eo-callout-warning'>"
            "<div class='eo-callout-title'><span class='eo-trust-tag eo-trust-human'>人間確認</span>管理者承認が必要</div>"
            "</div>",
            unsafe_allow_html=True,
        )


def _render_whatif_block(out: dict[str, Any]) -> None:
    scenarios = out.get("scenarios", []) or []
    if not scenarios:
        st.info("シナリオデータがありません。")
        return
    rows = []
    for s in scenarios:
        if not isinstance(s, dict):
            continue
        rows.append({
            "シナリオ": s.get("name", ""),
            "タイミング": s.get("timing", ""),
            "予測リスク": s.get("predicted_risk", ""),
            "推奨": "✓" if s.get("recommended") else "—",
            "根拠": s.get("rationale", ""),
            "生産影響": s.get("production_impact", ""),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ───────────────────────────────────────────────────────────────────────────
# Work Order tab
# ───────────────────────────────────────────────────────────────────────────

def _record_approval(action: str, prefix: str, comment: str) -> None:
    """Append an entry to the approval log so the audit trail is preserved
    even after re-runs. Also persists to Cosmos when configured so the trail
    survives container restarts."""
    from datetime import datetime, timezone, timedelta
    jst = timezone(timedelta(hours=9), name="JST")
    artifact = "Work Order" if prefix == "wo" else "Management Report"
    risk_level = st.session_state.risk.risk_level if st.session_state.risk else None
    st.session_state.approval_log.append({
        "time": datetime.now(jst).strftime("%Y-%m-%d %H:%M:%S JST"),
        "artifact": artifact,
        "action": action,
        "comment": comment.strip(),
        "equipment_id": st.session_state.equipment_id,
        "risk_level": risk_level or "—",
    })
    try:
        cosmos_store.record_approval(
            st.session_state.equipment_id,
            artifact=artifact,
            action=action,
            comment=comment.strip(),
            risk_level=risk_level,
        )
    except Exception:
        # Don't block the UI on persistence errors — the in-memory log still works.
        pass


def _approval_widget(prefix: str) -> None:
    st.markdown("### 承認")
    st.caption("AI による提案です。設備停止や部品交換などの判断は、必ず管理者が確認してください。")

    # Comment box — required when rejecting / requesting revision
    comment = st.text_area(
        "却下理由 / 修正指示（任意。却下・修正依頼時は記入推奨）",
        value="",
        height=80,
        key=f"{prefix}_comment",
        placeholder="例: 軸受温度の追加測定を先に実施してから判断したい / 停止判断は本日 17 時の会議後に再評価",
    )

    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    if c1.button("✅ 承認", key=f"{prefix}_approve"):
        st.session_state.approval_status = "承認済"
        _record_approval("承認", prefix, comment)
    if c2.button("🛠 修正依頼", key=f"{prefix}_revise"):
        st.session_state.approval_status = "修正依頼"
        _record_approval("修正依頼", prefix, comment)
    if c3.button("⛔ 却下", key=f"{prefix}_reject"):
        st.session_state.approval_status = "却下"
        _record_approval("却下", prefix, comment)
    badge = {
        "未承認": "🟡 未承認",
        "承認済": "🟢 承認済",
        "修正依頼": "🟠 修正依頼",
        "却下": "🔴 却下",
    }
    c4.markdown(f"**現在の状態**: {badge.get(st.session_state.approval_status, st.session_state.approval_status)}")

    # Audit trail — session log + persistent (Cosmos / local JSONL) export
    log = st.session_state.approval_log
    if log:
        with st.expander(f"承認ログ（このセッション {len(log)} 件）", expanded=False):
            log_df = pd.DataFrame(log)
            st.dataframe(log_df, use_container_width=True, hide_index=True)
            st.download_button(
                "ログを JSON でダウンロード（このセッション）",
                data=pd.DataFrame(log).to_json(orient="records", force_ascii=False, indent=2),
                file_name=f"approval_log_{st.session_state.equipment_id}.json",
                mime="application/json",
                key=f"{prefix}_dl_log",
            )

    # Persisted audit log — survives container restarts. Pulls from Cosmos when
    # configured, otherwise the local JSONL fallback.
    persisted = cosmos_store.recent_for_equipment(
        st.session_state.equipment_id,
        doc_types=["approval", "alert", "run"],
        limit=200,
    )
    if persisted:
        with st.expander(f"監査ログ（永続化済み {len(persisted)} 件）", expanded=False):
            st.caption(
                "永続化先: " + ("**Cosmos DB**" if cosmos_store.is_configured()
                                else "ローカル `_local_cosmos.jsonl`")
            )
            pdf_df = pd.DataFrame(persisted)
            st.dataframe(pdf_df, use_container_width=True, hide_index=True)
            c1, c2 = st.columns(2)
            c1.download_button(
                "JSON でダウンロード",
                data=pdf_df.to_json(orient="records", force_ascii=False, indent=2),
                file_name=f"audit_{st.session_state.equipment_id}.json",
                mime="application/json",
                key=f"{prefix}_dl_persist_json",
            )
            csv_buf = pdf_df.to_csv(index=False)
            # Prepend BOM so Excel-on-Windows reads UTF-8 correctly.
            c2.download_button(
                "CSV でダウンロード（Excel 用 BOM 付）",
                data=("﻿" + csv_buf).encode("utf-8"),
                file_name=f"audit_{st.session_state.equipment_id}.csv",
                mime="text/csv",
                key=f"{prefix}_dl_persist_csv",
            )


def _render_empty_state(icon: str, title: str, hint: str) -> None:
    st.markdown(
        f"<div class='eo-empty-state'>"
        f"<div class='eo-empty-icon'>{icon}</div>"
        f"<div class='eo-empty-title'>{title}</div>"
        f"<div class='eo-empty-hint'>{hint}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


_PRIORITY_PILL = {
    "Critical": "eo-pill-critical",
    "High":     "eo-pill-warning",
    "Medium":   "eo-pill-warning",
    "Low":      "eo-pill-normal",
}


def _clean_step(text: str) -> str:
    """Strip leading numbering ('1. ', '- ', '・') so we can use our own bullet."""
    s = str(text).strip()
    for prefix in ("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "10.", "・", "-"):
        if s.startswith(prefix):
            return s[len(prefix):].strip()
    return s


def _tab_work_order() -> None:
    pipeline = st.session_state.pipeline
    risk = st.session_state.risk
    equipment_id = st.session_state.equipment_id

    if pipeline is None:
        st.markdown("## Work Order")
        _render_empty_state(
            icon="📋",
            title="作業指示書はまだ生成されていません",
            hint="左サイドバーの **▶ Run Agents** を実行すると、Action Planning Agent が現場作業者向けの作業指示書を自動生成します。",
        )
        return

    plan = pipeline.action_plan.output if isinstance(pipeline.action_plan.output, dict) else {}
    priority = plan.get("priority", "—")
    deadline = plan.get("deadline_hours", "—")
    manager_required = bool(plan.get("manager_approval_required"))

    priority_pill_class = _PRIORITY_PILL.get(priority, "eo-pill-muted")
    risk_level = risk.risk_level if risk else "—"
    risk_pill_class = {
        "Critical": "eo-pill-critical",
        "Warning":  "eo-pill-warning",
        "Normal":   "eo-pill-normal",
    }.get(risk_level, "eo-pill-muted")

    # ── Header: equipment, priority, deadline, approval state
    st.markdown("## Work Order")
    st.caption("現場作業者が即座に着手できる作業指示書。承認後の実施が前提です。")

    approval_state = st.session_state.get("approval_status", "未承認")
    st.markdown(
        f"""
<div class='eo-action-panel'>
  <div class='eo-action-title'>Next Best Action</div>
  <div class='eo-action-headline'>{equipment_id} を {deadline}h 以内に点検</div>
  <div style='display:flex;flex-wrap:wrap;gap:8px;margin-bottom:4px;'>
    <span class='eo-pill {priority_pill_class}'>優先度 {priority}</span>
    <span class='eo-pill {risk_pill_class}'>Risk {risk_level}</span>
    <span class='eo-pill eo-pill-muted'>対応期限 {deadline}h 以内</span>
    <span class='eo-pill eo-pill-muted'>承認: {approval_state}</span>
    {"<span class='eo-pill eo-pill-warning'>管理者承認 必須</span>" if manager_required else ""}
  </div>
  <div class='eo-action-meta'>
    主要懸念: {risk.primary_concern if risk else "—"}
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    # ── Work steps as a numbered checklist
    st.markdown("<div class='eo-section-eyebrow'>作業手順 (Work Steps)</div>", unsafe_allow_html=True)
    work_steps = plan.get("work_steps", []) or []
    if work_steps:
        items_html: list[str] = []
        for i, step in enumerate(work_steps, start=1):
            items_html.append(
                f"<div class='eo-checklist-item'>"
                f"<div class='eo-checklist-num'>{i}</div>"
                f"<div class='eo-checklist-text'>{_clean_step(step)}</div>"
                f"</div>"
            )
        st.markdown(f"<div class='eo-checklist'>{''.join(items_html)}</div>", unsafe_allow_html=True)
    else:
        st.caption("作業手順は生成されませんでした。")

    # ── Tools / Parts — chip rows side by side
    tool_col, part_col = st.columns(2)
    with tool_col:
        st.markdown("<div class='eo-section-eyebrow'>必要工具</div>", unsafe_allow_html=True)
        tools = plan.get("required_tools", []) or ["（不要）"]
        chips = "".join(f"<span class='eo-chip eo-chip-tool'>🔧 {t}</span>" for t in tools)
        st.markdown(f"<div class='eo-chip-row'>{chips}</div>", unsafe_allow_html=True)
    with part_col:
        st.markdown("<div class='eo-section-eyebrow'>必要部品</div>", unsafe_allow_html=True)
        parts = plan.get("required_parts", []) or ["（不要）"]
        chips = "".join(f"<span class='eo-chip eo-chip-part'>📦 {p}</span>" for p in parts)
        st.markdown(f"<div class='eo-chip-row'>{chips}</div>", unsafe_allow_html=True)

    # ── Safety callouts — each note becomes a yellow warning callout
    safety = plan.get("safety_notes", []) or []
    if safety:
        st.markdown("<div class='eo-section-eyebrow'>安全上の注意</div>", unsafe_allow_html=True)
        for note in safety:
            st.markdown(
                f"<div class='eo-callout eo-callout-warning'>"
                f"<div class='eo-callout-title'>⚠ 安全</div>{note}</div>",
                unsafe_allow_html=True,
            )

    # ── Post-work recording (smaller info callout)
    post_recording = plan.get("post_work_recording", []) or []
    if post_recording:
        bullets = "".join(f"<li>{r}</li>" for r in post_recording)
        st.markdown(
            f"<div class='eo-callout eo-callout-info'>"
            f"<div class='eo-callout-title'>📒 作業後の記録項目</div>"
            f"<ul style='margin:4px 0 0;padding-left:18px;'>{bullets}</ul>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # ── Download (Markdown) — keep audit / portability path
    wo_md = report_generator.render_work_order(pipeline, equipment_id=equipment_id)
    with st.expander("📄 Markdown 版を表示 / ダウンロード", expanded=False):
        st.markdown(wo_md)
        st.download_button(
            "Markdown でダウンロード",
            data=wo_md,
            file_name=f"work_order_{equipment_id}.md",
            mime="text/markdown",
        )

    # ── Approval — the human-in-the-loop gate
    _approval_widget(prefix="wo")


# ───────────────────────────────────────────────────────────────────────────
# Management Report tab
# ───────────────────────────────────────────────────────────────────────────

def _tab_management_report() -> None:
    pipeline = st.session_state.pipeline
    risk = st.session_state.risk
    equipment_id = st.session_state.equipment_id

    st.markdown("## Management Report")
    st.caption("管理者（非専門家）向けの1ページ・エグゼクティブ報告です。")

    if pipeline is None or risk is None:
        _render_empty_state(
            icon="📑",
            title="報告書はまだ生成されていません",
            hint="左サイドバーの **▶ Run Agents** を実行すると、Root Cause / Action Planning Agent の出力から経営層向け1ページ報告が自動生成されます。",
        )
        return

    plan = pipeline.action_plan.output if isinstance(pipeline.action_plan.output, dict) else {}
    rc = pipeline.root_cause.output if isinstance(pipeline.root_cause.output, dict) else {}
    deadline = plan.get("deadline_hours", "—")
    priority = plan.get("priority", "—")

    # ── Executive summary hero
    risk_pill_class = {
        "Critical": "eo-pill-critical",
        "Warning":  "eo-pill-warning",
        "Normal":   "eo-pill-normal",
    }.get(risk.risk_level, "eo-pill-muted")
    approval_state = st.session_state.get("approval_status", "未承認")

    impact_one_liner = business_case.estimate(equipment_id, risk.risk_level).headline_one_liner()

    st.markdown(
        f"""
<div class='eo-hero'>
  <div style='display:flex;justify-content:space-between;align-items:flex-start;gap:24px;flex-wrap:wrap;'>
    <div style='flex:1;min-width:280px;'>
      <div class='eo-hero-eyebrow'>Executive Summary</div>
      <div class='eo-hero-title'>{equipment_id}</div>
      <div class='eo-hero-sub'>{impact_one_liner}</div>
      <div style='margin-top:14px;display:flex;gap:8px;flex-wrap:wrap;'>
        <span class='eo-pill {risk_pill_class}'>{risk.risk_level}</span>
        <span class='eo-pill eo-pill-muted'>承認: {approval_state}</span>
      </div>
    </div>
    <div style='text-align:right;min-width:160px;'>
      <div class='eo-hero-score'>{risk.health_score}</div>
      <div class='eo-hero-score-label'>Health Score / 100</div>
    </div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    # ── KPI strip
    st.markdown(
        f"""
<div class='eo-kpi-strip'>
  <div class='eo-kpi'>
    <div class='eo-kpi-label'>Equipment</div>
    <div class='eo-kpi-value'>{equipment_id}</div>
    <div class='eo-kpi-sub'>{risk.primary_concern}</div>
  </div>
  <div class='eo-kpi'>
    <div class='eo-kpi-label'>Risk Level</div>
    <div class='eo-kpi-value'>{risk.risk_level}</div>
    <div class='eo-kpi-sub'>優先度 {priority}</div>
  </div>
  <div class='eo-kpi'>
    <div class='eo-kpi-label'>Deadline</div>
    <div class='eo-kpi-value'>{deadline}h</div>
    <div class='eo-kpi-sub'>以内に対応</div>
  </div>
  <div class='eo-kpi'>
    <div class='eo-kpi-label'>Approval</div>
    <div class='eo-kpi-value'>{approval_state}</div>
    <div class='eo-kpi-sub'>Human-in-the-loop</div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    # ── 3-col evidence cards: Root Cause / Recommended Action / Business Impact
    causes_html = ""
    hypotheses = rc.get("root_cause_hypotheses", []) or []
    for h in hypotheses[:3]:
        if not isinstance(h, dict):
            continue
        causes_html += (
            f"<li><strong>{h.get('cause', '—')}</strong>"
            f" <span style='color:#64748b;'>（尤度 {h.get('likelihood', '?')}）</span><br>"
            f"<span style='color:#475569;font-size:12.5px;'>{h.get('reason', '')}</span></li>"
        )
    if not causes_html:
        causes_html = "<li>原因仮説は生成されませんでした。</li>"

    actions = [_clean_step(s) for s in (plan.get("work_steps") or [])[:4]] or ["（提案なし）"]
    actions_html = "".join(f"<li>{a}</li>" for a in actions)

    if risk.risk_level == "Critical":
        impact_bullets = [
            "生産停止リスク: <strong>高</strong>",
            "安全リスク: 中",
            "下流設備への波及可能性あり",
        ]
    elif risk.risk_level == "Warning":
        impact_bullets = [
            "生産停止リスク: <strong>中</strong>",
            "下流バッファで吸収可能",
            "悪化次第で計画外停止の可能性",
        ]
    else:
        impact_bullets = ["影響なし（通常運転継続）"]
    impact_html = "".join(f"<li>{b}</li>" for b in impact_bullets)

    c1, c2, c3 = st.columns(3)
    c1.markdown(
        f"<div class='eo-card'>"
        f"<div class='eo-card-title'><span class='eo-trust-tag eo-trust-ai'>AI推論</span>原因仮説</div>"
        f"<ul>{causes_html}</ul></div>",
        unsafe_allow_html=True,
    )
    c2.markdown(
        f"<div class='eo-card'>"
        f"<div class='eo-card-title'><span class='eo-trust-tag eo-trust-ai'>AI推論</span>推奨対応</div>"
        f"<ul>{actions_html}</ul></div>",
        unsafe_allow_html=True,
    )
    c3.markdown(
        f"<div class='eo-card'>"
        f"<div class='eo-card-title'><span class='eo-trust-tag eo-trust-ai'>AI推論</span>業務影響</div>"
        f"<ul>{impact_html}</ul></div>",
        unsafe_allow_html=True,
    )

    # ── Uncertainty callout
    uncertainty = rc.get("uncertainty") or ""
    if uncertainty:
        st.markdown(
            f"<div class='eo-callout eo-callout-info'>"
            f"<div class='eo-callout-title'><span class='eo-trust-tag eo-trust-uncert'>不確実性</span>判断のばらつき要因</div>"
            f"{uncertainty}</div>",
            unsafe_allow_html=True,
        )

    # ── Markdown version (downloadable) — preserved for audit / portability
    mr_md = report_generator.render_management_report(pipeline, equipment_id=equipment_id)
    with st.expander("📄 Markdown 版を表示 / ダウンロード", expanded=False):
        st.markdown(mr_md)
        st.download_button(
            "Markdown でダウンロード",
            data=mr_md,
            file_name=f"management_report_{equipment_id}.md",
            mime="text/markdown",
        )

    _approval_widget(prefix="mr")


# ───────────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────────

def main() -> None:
    _init_state()
    _inject_css()
    _sidebar()

    st.markdown(
        "<h1 style='margin-top:0;'>🛠 EdgeOps Command Agent</h1>"
        "<p style='color:var(--eo-muted);margin-top:-8px;'>点検データを、判断・行動・報告に変換する保全AIエージェント</p>",
        unsafe_allow_html=True,
    )

    tabs = st.tabs([
        "🏠 Command Center",
        "📥 Data Upload",
        "📈 Signal Analysis",
        "🖼 Vision Inspection",
        "🤖 Agent Reasoning",
        "📋 Work Order",
        "📑 Management Report",
    ])
    with tabs[0]:
        _tab_command_center()
    with tabs[1]:
        _tab_data_upload()
    with tabs[2]:
        _tab_signal_analysis()
    with tabs[3]:
        _tab_vision()
    with tabs[4]:
        _tab_agent_reasoning()
    with tabs[5]:
        _tab_work_order()
    with tabs[6]:
        _tab_management_report()


if __name__ == "__main__":
    main()
