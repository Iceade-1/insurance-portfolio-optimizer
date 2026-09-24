"""Return maximisation under a Solvency II market-risk budget, as a second-order cone program.

    maximise    mu' w
    subject to  sum(w) = 1, 0 <= w_i <= cap_i
                sum of BBB-and-below weights <= max_bbb_and_below
                |A * D_A(w) - L * D_L| / A <= tau                      (dollar-duration gap)
                t_s >= loss_s(w), t_s >= 0                  for s in {up, down}
                || chol(C_s)' [t_s, eq(w), prop(w), spread(w)] ||_2 <= B   for s in {up, down}

Why it is convex: every sub-module charge is affine and non-negative in w (the interest
charge through the auxiliary t_s), and all correlations are non-negative, so
sqrt(s' C s) is a norm that is non-decreasing in each component. Each budget
constraint is therefore a second-order cone.

The regulation aggregates only in the scenario that gives the larger interest charge
(Art. 164-165). Requiring the budget in both scenarios is a conservative, convex
version of that rule: any feasible portfolio's true SCR is within the budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cvxpy as cp
import numpy as np
import pandas as pd

from iro.data_ecb import YieldCurve
from iro.returns import expected_returns
from iro.scr import C_DOWN, C_UP, ScrModel

L_UP = np.linalg.cholesky(C_UP)
L_DOWN = np.linalg.cholesky(C_DOWN)


@dataclass
class Result:
    status: str                                 # 'optimal', 'infeasible', ...
    budget: float
    weights: pd.Series | None = None
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.weights is not None


class Problem:
    """Everything needed to optimise and evaluate portfolios for one set of inputs."""

    def __init__(self, assets: pd.DataFrame, curve: YieldCurve, assumptions: dict[str, float]):
        self.assets = assets
        self.curve = curve
        self.assumptions = dict(assumptions)
        self.model = ScrModel.build(assets, curve, assumptions)
        self.mu = expected_returns(assets, curve, assumptions)
        self.durations = assets["duration"].to_numpy(dtype=float)
        self.sub_a = ((assets["asset_class"] == "corp") & (assets["cqs"] >= 3)).to_numpy()

        self.A = assumptions["assets_mv"]
        self.L = assumptions["liabilities_be"]
        self.D_L = assumptions["liab_duration"]
        self.liab_rate = float(curve.rate(self.D_L))

    # -- evaluation ----------------------------------------------------------
    def metrics(self, w) -> dict[str, float]:
        """Portfolio metrics, keyed like the `run_metrics` table."""
        w = np.asarray(w, dtype=float)
        m = self.model.market(w)
        scr = m["scr_mkt"]
        charges = m["charges"]
        exp_return = float(self.mu @ w)
        own_funds = self.A - self.L
        excess = exp_return * self.A - self.liab_rate * self.L   # return above the cost of liabilities
        return {
            "exp_return": exp_return,
            "scr_int": charges["interest"],
            "scr_eq": charges["equity"],
            "scr_prop": charges["property"],
            "scr_spread": charges["spread"],
            "scr_mkt": scr,
            "divers_benefit": m["diversification"],
            "solvency_ratio": own_funds / scr if scr > 0 else float("inf"),
            "ro_scr": excess / scr if scr > 0 else float("inf"),
            "duration_gap": float((self.A * self.durations @ w - self.L * self.D_L) / self.A),
        }

    # -- optimisation --------------------------------------------------------
    def _base(self, caps=None, max_bbb_and_below=None, duration_gap_tol=None):
        n = len(self.mu)
        caps = self.assets["cap"].to_numpy(dtype=float) if caps is None else np.asarray(caps, dtype=float)
        max_sub_a = self.assumptions["max_bbb_and_below"] if max_bbb_and_below is None else max_bbb_and_below
        tau = self.assumptions["duration_gap_tol"] if duration_gap_tol is None else duration_gap_tol

        w = cp.Variable(n, name="w")
        t_up = cp.Variable(nonneg=True, name="t_up")
        t_down = cp.Variable(nonneg=True, name="t_down")
        m = self.model
        eq, prop, spread = m.eq_vec @ w, m.prop_vec @ w, m.spread_vec @ w
        cons = [
            cp.sum(w) == 1,
            w >= 0,
            w <= caps,
            cp.sum(w[self.sub_a]) <= max_sub_a,
            cp.abs(self.A * self.durations @ w - self.L * self.D_L) <= tau * self.A,
            t_up >= m.ir_up_vec @ w - m.ir_up_liab,
            t_down >= m.ir_down_vec @ w - m.ir_down_liab,
        ]
        scr_up = cp.norm(L_UP.T @ cp.hstack([t_up, eq, prop, spread]))
        scr_down = cp.norm(L_DOWN.T @ cp.hstack([t_down, eq, prop, spread]))
        return w, cons, scr_up, scr_down

    def _result(self, prob: cp.Problem, w: cp.Variable, budget: float) -> Result:
        if prob.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
            return Result(status=prob.status, budget=budget)
        x = np.clip(w.value, 0.0, None)
        x = x / x.sum()
        return Result(
            status=prob.status,
            budget=budget,
            weights=pd.Series(x, index=self.assets.index, name="weight"),
            metrics=self.metrics(x),
        )

    def solve(self, budget: float, **limits) -> Result:
        """Maximise expected return with SCR market <= budget (EUR m)."""
        w, cons, scr_up, scr_down = self._base(**limits)
        prob = cp.Problem(cp.Maximize(self.mu @ w), [*cons, scr_up <= budget, scr_down <= budget])
        prob.solve(solver=cp.CLARABEL)
        return self._result(prob, w, budget)

    def min_scr(self, **limits) -> Result:
        """Portfolio with the lowest (conservative) SCR that satisfies the other constraints."""
        w, cons, scr_up, scr_down = self._base(**limits)
        prob = cp.Problem(cp.Minimize(cp.maximum(scr_up, scr_down)), cons)
        prob.solve(solver=cp.CLARABEL)
        return self._result(prob, w, budget=float(prob.value) if prob.value is not None else float("nan"))

    def max_return(self, **limits) -> Result:
        """Portfolio with the highest return when capital is not constrained."""
        w, cons, _, _ = self._base(**limits)
        prob = cp.Problem(cp.Maximize(self.mu @ w), cons)
        prob.solve(solver=cp.CLARABEL)
        return self._result(prob, w, budget=float("inf"))
