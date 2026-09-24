"""Expected annual return per bucket, built on the AAA curve."""

from __future__ import annotations

import numpy as np
import pandas as pd

from iro.data_ecb import YieldCurve


def expected_returns(assets: pd.DataFrame, curve: YieldCurve, assumptions: dict[str, float]) -> np.ndarray:
    """Carry-based expected returns (decimals), in the order of `assets`.

    - cash and govies: AAA spot rate at the bucket's duration
    - corporates: AAA rate + credit spread - expected loss (PD x LGD)
    - equity: 10y AAA rate + equity risk premium
    - property: 10y AAA rate + property premium
    """
    r10 = float(curve.rate(10.0))
    mu = []
    for cls, dur, spread_bp, el_bp in zip(
        assets["asset_class"], assets["duration"], assets["spread_bp"], assets["el_bp"]
    ):
        if cls in ("cash", "gov"):
            mu.append(float(curve.rate(dur)))
        elif cls == "corp":
            mu.append(float(curve.rate(dur)) + (spread_bp - el_bp) / 1e4)
        elif cls == "equity":
            mu.append(r10 + assumptions["equity_risk_premium"])
        elif cls == "property":
            mu.append(r10 + assumptions["property_premium"])
        else:
            raise ValueError(f"unknown asset class {cls!r}")
    return np.array(mu)
