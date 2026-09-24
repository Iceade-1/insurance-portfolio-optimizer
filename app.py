"""Streamlit front end: `streamlit run app.py`.

Environment variables:
    IRO_DB       path of the SQLite database (default: iro.db next to this file)
    IRO_OFFLINE  set to 1 to skip the ECB call (cache or fallback curve only)
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from iro import data_ecb, db
from iro.frontier import efficient_frontier, max_ro_scr
from iro.optimizer import Problem
from iro.scr import spread_factor

DB_PATH = os.environ.get("IRO_DB", str(Path(__file__).with_name("iro.db")))
OFFLINE = os.environ.get("IRO_OFFLINE") == "1"
EDITABLE = ["name", "duration", "spread_bp", "el_bp", "cap"]

st.set_page_config(page_title="Insurance portfolio optimiser", page_icon="📈", layout="wide")


# --- data ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner="Loading the ECB AAA curve…")
def load_curve(refresh_token: int) -> tuple[str, dict[float, float], str]:
    conn = db.init_db(DB_PATH)
    try:
        curve = data_ecb.load_curve(conn, refresh=not OFFLINE)
    finally:
        conn.close()
    return curve.obs_date, dict(zip(curve.maturities, curve.rates)), curve.source


@st.cache_data(show_spinner="Computing the efficient frontier…")
def compute_frontier(assets: pd.DataFrame, curve_points: tuple, assumptions: dict) -> pd.DataFrame:
    curve = data_ecb.YieldCurve.from_points("", dict(curve_points), "")
    return efficient_frontier(Problem(assets, curve, assumptions), n=30)


def pct(x: float, digits: int = 2) -> str:
    return f"{x:.{digits}%}"


conn = db.init_db(DB_PATH)
base_assets = db.load_assets(conn)
base_assumptions = db.load_assumptions(conn)

if "curve_token" not in st.session_state:
    st.session_state.curve_token = 0
obs_date, points, source = load_curve(st.session_state.curve_token)
curve = data_ecb.YieldCurve.from_points(obs_date, points, source)

# --- sidebar: balance sheet and limits -------------------------------------------

st.sidebar.header("Balance sheet (EUR m)")
assumptions = dict(base_assumptions)
assumptions["assets_mv"] = st.sidebar.number_input("Assets A", 100.0, 100_000.0, base_assumptions["assets_mv"], 50.0)
assumptions["liabilities_be"] = st.sidebar.number_input(
    "Best-estimate liabilities L", 0.0, assumptions["assets_mv"] * 0.99, min(base_assumptions["liabilities_be"], assumptions["assets_mv"] * 0.99), 50.0
)
assumptions["liab_duration"] = st.sidebar.slider("Liability duration D_L (years)", 1.0, 25.0, base_assumptions["liab_duration"], 0.5)

st.sidebar.header("Investment limits")
assumptions["duration_gap_tol"] = st.sidebar.slider("Max duration gap τ (years)", 0.1, 3.0, base_assumptions["duration_gap_tol"], 0.1)
assumptions["max_bbb_and_below"] = st.sidebar.slider("Max BBB and below", 0.0, 0.6, base_assumptions["max_bbb_and_below"], 0.05)

st.sidebar.header("Return assumptions")
assumptions["equity_risk_premium"] = st.sidebar.slider("Equity risk premium", 0.0, 0.10, base_assumptions["equity_risk_premium"], 0.005, format="%.3f")
assumptions["property_premium"] = st.sidebar.slider("Property premium", 0.0, 0.08, base_assumptions["property_premium"], 0.005, format="%.3f")

st.sidebar.caption(f"Curve: ECB AAA spot, {obs_date} ({source})")
if st.sidebar.button("Refresh ECB curve", disabled=OFFLINE):
    st.session_state.curve_token += 1
    st.rerun()

st.title("Solvency II-aware insurance portfolio optimiser")
st.caption(
    "Maximise expected return under a Solvency II market-risk SCR budget (standard formula: "
    "interest rate, equity, property, spread), with duration matching and credit limits."
)

tab_inputs, tab_portfolio, tab_frontier, tab_capital = st.tabs(
    ["Inputs", "Optimal portfolio", "Efficient frontier", "Capital decomposition"]
)

# --- tab 1: inputs -------------------------------------------------------------------

with tab_inputs:
    st.subheader("Investment universe")
    st.caption("Durations, spreads, expected losses (bp) and caps are editable assumptions for this session.")
    edited = st.data_editor(
        base_assets[["asset_class", "cqs", *EDITABLE]],
        disabled=["asset_class", "cqs"],
        column_config={
            "cqs": st.column_config.NumberColumn("CQS", format="%d"),
            "duration": st.column_config.NumberColumn("duration (y)", min_value=0.0, max_value=40.0, step=0.25, format="%.2f"),
            "spread_bp": st.column_config.NumberColumn("spread (bp)", min_value=0.0, step=5.0),
            "el_bp": st.column_config.NumberColumn("exp. loss (bp)", min_value=0.0, step=1.0),
            "cap": st.column_config.NumberColumn("max weight", min_value=0.0, max_value=1.0, step=0.05, format="%.2f"),
        },
        use_container_width=True,
    )
    left, _ = st.columns([3, 2])
    with left:
        st.subheader("AAA spot curve")
        grid = np.linspace(0.25, 30, 120)
        fig = go.Figure(go.Scatter(x=grid, y=curve.rate(grid) * 100, mode="lines", name="spot"))
        fig.add_trace(go.Scatter(x=curve.maturities, y=curve.rates * 100, mode="markers", name="ECB tenors"))
        fig.update_layout(xaxis_title="maturity (years)", yaxis_title="rate (%)", height=360, margin=dict(t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

assets = base_assets.copy()
assets[EDITABLE] = edited[EDITABLE]
problem = Problem(assets, curve, assumptions)

try:
    frontier = compute_frontier(assets, tuple(sorted(points.items())), assumptions)
except ValueError as exc:
    st.error(f"No portfolio satisfies these limits: {exc}. Relax the duration gap or the caps.")
    st.stop()

b_min = float(frontier["budget"].min())
b_max = float(frontier["budget"].max())
budget = st.sidebar.slider(
    "SCR market budget (EUR m)",
    math.ceil(b_min * 10) / 10,
    max(math.ceil(b_max * 10) / 10, math.ceil(b_min * 10) / 10 + 0.1),
    float(np.clip(60.0, math.ceil(b_min * 10) / 10, math.ceil(b_max * 10) / 10)),
    0.5,
)
result = problem.solve(budget)
if not result.ok:
    st.error(f"Solver status: {result.status}. Try a larger budget.")
    st.stop()
w, m = result.weights, result.metrics
market = problem.model.market(w.to_numpy())

# --- tab 2: optimal portfolio -------------------------------------------------------

with tab_portfolio:
    k = st.columns(5)
    k[0].metric("Expected return", pct(m["exp_return"]))
    k[1].metric("SCR market", f"{m['scr_mkt']:.1f}")
    k[2].metric("Solvency ratio (market only)", pct(m["solvency_ratio"], 0))
    k[3].metric("Return on SCR", pct(m["ro_scr"], 1))
    k[4].metric("Duration gap (y)", f"{m['duration_gap']:+.2f}")

    table = pd.DataFrame({
        "bucket": assets["name"],
        "weight": w,
        "amount (EUR m)": w * assumptions["assets_mv"],
        "expected return": problem.mu,
        "spread factor": [
            spread_factor(d, q) if c == "corp" else 0.0
            for c, d, q in zip(assets["asset_class"], assets["duration"], assets["cqs"])
        ],
    })
    fig = go.Figure(go.Bar(x=table.index, y=table["weight"] * 100, text=(table["weight"] * 100).round(1), textposition="outside"))
    fig.update_layout(yaxis_title="weight (%)", height=380, margin=dict(t=20, b=10))
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(
        table.style.format({"weight": "{:.1%}", "amount (EUR m)": "{:.1f}", "expected return": "{:.2%}", "spread factor": "{:.1%}"}),
        use_container_width=True,
    )
    if st.button("Save this run"):
        run_id = db.save_run(conn, "single", {"budget": budget, **assumptions}, w.to_dict(), m)
        st.success(f"Saved as run {run_id}.")

# --- tab 3: efficient frontier ------------------------------------------------------

with tab_frontier:
    best = max_ro_scr(frontier)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=frontier["scr_mkt"], y=frontier["exp_return"] * 100, mode="lines+markers", name="efficient frontier"))
    fig.add_trace(go.Scatter(x=[m["scr_mkt"]], y=[m["exp_return"] * 100], mode="markers", marker=dict(size=14, symbol="star"), name="current portfolio"))
    fig.add_trace(go.Scatter(x=[best["scr_mkt"]], y=[best["exp_return"] * 100], mode="markers", marker=dict(size=12, symbol="diamond"), name="max return on SCR"))
    fig.update_layout(xaxis_title="SCR market (EUR m)", yaxis_title="expected return (%)", height=420, margin=dict(t=20, b=10))
    st.plotly_chart(fig, use_container_width=True)

    c1, c2 = st.columns(2)
    fig = go.Figure(go.Scatter(x=frontier["scr_mkt"], y=frontier["ro_scr"] * 100, mode="lines"))
    fig.update_layout(title="Return on SCR along the frontier", xaxis_title="SCR market", yaxis_title="RoSCR (%)", height=320, margin=dict(t=40, b=10))
    c1.plotly_chart(fig, use_container_width=True)
    ids = list(assets.index)
    fig = go.Figure([go.Scatter(x=frontier["scr_mkt"], y=frontier[i] * 100, stackgroup="w", name=i) for i in ids])
    fig.update_layout(title="Allocation along the frontier", xaxis_title="SCR market", yaxis_title="weight (%)", height=320, margin=dict(t=40, b=10))
    c2.plotly_chart(fig, use_container_width=True)

    with st.expander("Saved runs (SQLite)"):
        runs = db.load_runs(conn)
        if len(runs):
            st.dataframe(runs.drop(columns="params_json"), use_container_width=True)
        else:
            st.caption("No saved runs yet.")

# --- tab 4: capital decomposition ---------------------------------------------------

with tab_capital:
    charges = market["charges"]
    if charges["interest"] > 0:
        st.caption(
            f"Binding interest-rate scenario: **rates {market['scenario']}** "
            f"(correlation A = {0.0 if market['scenario'] == 'up' else 0.5}, Art. 164(3))"
        )
    else:
        st.caption(
            "No interest-rate charge: own funds rise in both the up and the down scenario, "
            "because assets and liabilities are duration-matched along the shocked curve."
        )
    fig = go.Figure(go.Waterfall(
        x=["interest", "equity", "property", "spread", "diversification", "SCR market"],
        measure=["relative"] * 5 + ["total"],
        y=[charges["interest"], charges["equity"], charges["property"], charges["spread"], -market["diversification"], 0],
        text=[f"{v:.1f}" for v in [*charges.values(), -market["diversification"], market["scr_mkt"]]],
        textposition="outside",
    ))
    fig.update_layout(yaxis_title="EUR m", height=400, margin=dict(t=20, b=10))
    st.plotly_chart(fig, use_container_width=True)

    alloc = problem.model.by_bucket(w.to_numpy())
    fig = go.Figure([go.Bar(x=alloc.index, y=alloc[col], name=col) for col in alloc.columns])
    fig.update_layout(barmode="relative", title="SCR market allocated to buckets (Euler)", yaxis_title="EUR m", height=400, margin=dict(t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)

conn.close()
