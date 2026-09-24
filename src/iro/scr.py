"""Solvency II standard formula, market risk module (simplified).

Sub-modules covered: interest rate, equity (type 1), property and spread on bonds.
Currency and concentration risk are out of scope. Every sub-module charge is
linear in the portfolio weights, which is what lets the optimiser stay convex.

References are to Commission Delegated Regulation (EU) 2015/35, as adopted
(OJ L 12, 17.1.2015). Values were checked against the Official Journal text.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from iro.data_ecb import YieldCurve

SUBMODULES = ("interest", "equity", "property", "spread")

# --- Spread risk on bonds, Art. 176(3) -------------------------------------
# For each credit quality step: (a_i, b_i) per duration band. Band lower
# bounds are 0, 5, 10, 15, 20; stress = a + b * (dur - lower bound), with
# a = 0 in the first band. CQS 5 covers steps 5 and 6.
SPREAD_BANDS = (0.0, 5.0, 10.0, 15.0, 20.0)
SPREAD_TABLE = {
    0: ((0.0, 0.009), (0.045, 0.005), (0.070, 0.005), (0.095, 0.005), (0.120, 0.005)),
    1: ((0.0, 0.011), (0.055, 0.006), (0.084, 0.005), (0.109, 0.005), (0.134, 0.005)),
    2: ((0.0, 0.014), (0.070, 0.007), (0.105, 0.005), (0.130, 0.005), (0.155, 0.005)),
    3: ((0.0, 0.025), (0.125, 0.015), (0.200, 0.010), (0.250, 0.010), (0.300, 0.005)),
    4: ((0.0, 0.045), (0.225, 0.025), (0.350, 0.018), (0.440, 0.005), (0.465, 0.005)),
    5: ((0.0, 0.075), (0.375, 0.042), (0.585, 0.005), (0.610, 0.005), (0.635, 0.005)),
}

# --- Interest rate risk, Art. 166-167: relative shocks by maturity ---------
# Linear interpolation between points; flat below 1y and beyond 90y.
IR_MATURITIES = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 90], dtype=float)
IR_UP = np.array([.70, .70, .64, .59, .55, .52, .49, .47, .44, .42, .39, .37, .35, .34, .33, .31, .30, .29, .27, .26, .20])
IR_DOWN = np.array([.75, .65, .56, .50, .46, .42, .39, .36, .33, .31, .30, .29, .28, .28, .27, .28, .28, .28, .29, .29, .20])
IR_UP_FLOOR = 0.01            # Art. 166(2): the increase is at least one percentage point

EQUITY_TYPE1_SHOCK = 0.39     # Art. 169(1)(b), symmetric adjustment ignored
PROPERTY_SHOCK = 0.25         # Art. 174

# --- Aggregation, Art. 164(3): order interest, equity, property, spread ----
def _corr(a: float) -> np.ndarray:
    return np.array([
        [1.0, a,    a,    a],
        [a,   1.0,  0.75, 0.75],
        [a,   0.75, 1.0,  0.5],
        [a,   0.75, 0.5,  1.0],
    ])

C_UP = _corr(0.0)     # A = 0 when the rate-increase scenario gives the interest charge
C_DOWN = _corr(0.5)   # A = 0.5 otherwise


def spread_factor(duration: float, cqs: int) -> float:
    """Art. 176(3) risk factor for a rated bond; duration is floored at 1 year (Art. 176(2))."""
    dur = max(float(duration), 1.0)
    band = int(np.searchsorted(SPREAD_BANDS, dur, side="left")) - 1
    band = min(max(band, 0), len(SPREAD_BANDS) - 1)
    a, b = SPREAD_TABLE[int(cqs)][band]
    stress = a + b * (dur - SPREAD_BANDS[band])
    return min(stress, 1.0)


def ir_shock_up(maturity):
    return np.interp(maturity, IR_MATURITIES, IR_UP)


def ir_shock_down(maturity):
    return np.interp(maturity, IR_MATURITIES, IR_DOWN)


def rate_moves(curve: YieldCurve, maturity) -> tuple[np.ndarray, np.ndarray]:
    """Absolute rate changes (up >= 0, down <= 0) at `maturity` under the two scenarios."""
    r = np.asarray(curve.rate(maturity), dtype=float)
    up = np.maximum(r * ir_shock_up(maturity), IR_UP_FLOOR)
    down = np.where(r > 0, -r * ir_shock_down(maturity), 0.0)   # Art. 167(2): nil if r <= 0
    return up, down


@dataclass(frozen=True)
class ScrModel:
    """Linear exposures of each sub-module to the portfolio weights.

    For a weight vector w (summing to 1):
        spread   = spread_vec @ w
        equity   = eq_vec @ w
        property = prop_vec @ w
        loss_up   = ir_up_vec @ w - ir_up_liab     (loss in own funds, rates up)
        loss_down = ir_down_vec @ w - ir_down_liab (loss in own funds, rates down)
    Interest-rate effects use a first-order duration approximation.
    """

    asset_ids: tuple[str, ...]
    spread_vec: np.ndarray
    eq_vec: np.ndarray
    prop_vec: np.ndarray
    ir_up_vec: np.ndarray
    ir_down_vec: np.ndarray
    ir_up_liab: float
    ir_down_liab: float

    @classmethod
    def build(cls, assets: pd.DataFrame, curve: YieldCurve, assumptions: dict[str, float]) -> ScrModel:
        A = assumptions["assets_mv"]
        L = assumptions["liabilities_be"]
        D_L = assumptions["liab_duration"]
        cls_ = assets["asset_class"]
        dur = assets["duration"].to_numpy(dtype=float)

        # EEA govies in domestic currency: 0 % (Art. 180(2)); cash at bank is out of scope (Art. 176(1)).
        stress = np.array([
            spread_factor(d, q) if c == "corp" else 0.0
            for c, d, q in zip(cls_, dur, assets["cqs"])
        ])
        up, down = rate_moves(curve, dur)
        has_rate = (dur > 0).astype(float)
        liab_up, liab_down = rate_moves(curve, D_L)

        return cls(
            asset_ids=tuple(assets.index),
            spread_vec=A * stress,
            eq_vec=A * EQUITY_TYPE1_SHOCK * (cls_ == "equity").to_numpy(dtype=float),
            prop_vec=A * PROPERTY_SHOCK * (cls_ == "property").to_numpy(dtype=float),
            # dV = -D * V * dy, so a loss of +D * V * dy on assets and -D * L * dy on liabilities
            ir_up_vec=A * dur * up * has_rate,
            ir_down_vec=A * dur * down * has_rate,
            ir_up_liab=float(L * D_L * liab_up),
            ir_down_liab=float(L * D_L * liab_down),
        )

    # -- per-sub-module charges ------------------------------------------------
    def components(self, w) -> dict[str, float]:
        w = np.asarray(w, dtype=float)
        loss_up = float(self.ir_up_vec @ w - self.ir_up_liab)
        loss_down = float(self.ir_down_vec @ w - self.ir_down_liab)
        return {
            "int_up": max(loss_up, 0.0),
            "int_down": max(loss_down, 0.0),
            "equity": float(self.eq_vec @ w),
            "property": float(self.prop_vec @ w),
            "spread": float(self.spread_vec @ w),
        }

    # -- aggregation -----------------------------------------------------------
    def market(self, w) -> dict:
        """SCR market (Art. 164-165): the interest charge is the larger scenario, which sets A."""
        c = self.components(w)
        scenario = "up" if c["int_up"] >= c["int_down"] else "down"
        s = np.array([c[f"int_{scenario}"], c["equity"], c["property"], c["spread"]])
        corr = C_UP if scenario == "up" else C_DOWN
        scr = float(np.sqrt(s @ corr @ s))
        return {
            "scenario": scenario,
            "charges": dict(zip(SUBMODULES, s)),
            "scr_mkt": scr,
            "diversification": float(s.sum() - scr),
            "corr": corr,
        }

    def by_bucket(self, w) -> pd.DataFrame:
        """Euler allocation of SCR market to buckets and sub-modules; the cells sum to SCR market.

        Sub-module k receives s_k (C s)_k / SCR; within k, the allocation follows each
        bucket's standalone charge. For interest, a 'Liabilities' row carries the offset.
        """
        w = np.asarray(w, dtype=float)
        m = self.market(w)
        s = np.array(list(m["charges"].values()))
        scr = m["scr_mkt"]
        euler = s * (m["corr"] @ s) / scr if scr > 0 else np.zeros_like(s)

        ir_vec = self.ir_up_vec if m["scenario"] == "up" else self.ir_down_vec
        ir_liab = self.ir_up_liab if m["scenario"] == "up" else self.ir_down_liab
        standalone = {
            "interest": np.append(ir_vec * w, -ir_liab),
            "equity": np.append(self.eq_vec * w, 0.0),
            "property": np.append(self.prop_vec * w, 0.0),
            "spread": np.append(self.spread_vec * w, 0.0),
        }
        out = {}
        for k, name in enumerate(SUBMODULES):
            charge = standalone[name]
            total = charge.sum()
            out[name] = charge / total * euler[k] if s[k] > 0 and total != 0 else np.zeros_like(charge)
        return pd.DataFrame(out, index=[*self.asset_ids, "Liabilities"])
