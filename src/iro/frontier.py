"""Return / SCR efficient frontier and return on capital."""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from iro import db
from iro.optimizer import Problem


def efficient_frontier(problem: Problem, n: int = 25, **limits) -> pd.DataFrame:
    """Sweep the SCR budget from the minimum achievable SCR to the SCR of the
    unconstrained max-return portfolio.

    One row per point: budget, status, metrics, then one weight column per bucket.
    RoSCR = (expected return x A - AAA rate at D_L x L) / SCR market, i.e. the excess
    return over the cost of the liabilities per euro of capital.
    """
    lo = problem.min_scr(**limits)
    if not lo.ok:
        raise ValueError(f"no feasible portfolio under these limits ({lo.status})")
    hi = problem.max_return(**limits)
    b_min = lo.budget
    b_max = max(hi.metrics["scr_mkt"], b_min)

    rows = []
    for budget in np.linspace(b_min, b_max, n):
        # nudge the first point up so rounding in the min-SCR solve cannot make it infeasible
        res = problem.solve(budget * (1 + 1e-6) if budget == b_min else budget, **limits)
        row = {"budget": budget, "status": res.status}
        if res.ok:
            row.update(res.metrics)
            row.update(res.weights.to_dict())
        rows.append(row)
    return pd.DataFrame(rows)


def max_ro_scr(frontier: pd.DataFrame) -> pd.Series:
    """The frontier point with the highest return on SCR."""
    feasible = frontier[frontier["status"].isin(["optimal", "optimal_inaccurate"])]
    return feasible.loc[feasible["ro_scr"].idxmax()]


def save_frontier(conn: sqlite3.Connection, frontier: pd.DataFrame, asset_ids, params: dict | None = None) -> list[int]:
    """Store every feasible point as a 'frontier' run; returns the run ids."""
    ids = []
    for _, row in frontier.iterrows():
        if row["status"] not in ("optimal", "optimal_inaccurate"):
            continue
        weights = {a: row[a] for a in asset_ids}
        metrics = {k: row[k] for k in db.RUN_METRIC_COLUMNS}
        ids.append(db.save_run(conn, "frontier", {**(params or {}), "budget": row["budget"]}, weights, metrics))
    return ids
