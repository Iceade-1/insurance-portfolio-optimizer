"""Euro-area AAA spot curve from the ECB Data Portal, with a local cache and a fallback."""

from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass

import numpy as np
import requests

# ECB tenor code -> maturity in years
TENORS = {
    "SR_3M": 0.25, "SR_1Y": 1.0, "SR_2Y": 2.0, "SR_3Y": 3.0, "SR_5Y": 5.0,
    "SR_7Y": 7.0, "SR_10Y": 10.0, "SR_15Y": 15.0, "SR_20Y": 20.0, "SR_30Y": 30.0,
}

ECB_URL = (
    "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM."
    + "+".join(TENORS)
    + "?format=csvdata&lastNObservations=1"
)

# ECB AAA spot curve observed on 2026-09-23 (Svensson, continuous compounding).
# Used when the API is unreachable and nothing is cached, e.g. during an offline demo.
FALLBACK_DATE = "2026-09-23"
FALLBACK_RATES = {
    0.25: 0.026414, 1.0: 0.030217, 2.0: 0.032183, 3.0: 0.032803, 5.0: 0.033295,
    7.0: 0.033964, 10.0: 0.035237, 15.0: 0.036994, 20.0: 0.037870, 30.0: 0.037627,
}


@dataclass(frozen=True)
class YieldCurve:
    obs_date: str
    maturities: np.ndarray   # years, ascending
    rates: np.ndarray        # decimals
    source: str              # 'ecb', 'cache' or 'fallback'

    @classmethod
    def from_points(cls, obs_date: str, points: dict[float, float], source: str) -> YieldCurve:
        mats = np.array(sorted(points), dtype=float)
        return cls(obs_date, mats, np.array([points[m] for m in mats]), source)

    def rate(self, maturity):
        """Spot rate at `maturity` (years): linear interpolation, flat beyond the ends."""
        return np.interp(maturity, self.maturities, self.rates)


def parse_ecb_csv(text: str) -> tuple[str, dict[float, float]]:
    """Parse the ECB csvdata response into (observation date, {maturity: rate})."""
    points, dates = {}, set()
    for row in csv.DictReader(io.StringIO(text)):
        tenor = row["DATA_TYPE_FM"]
        if tenor in TENORS and row["OBS_VALUE"]:
            points[TENORS[tenor]] = float(row["OBS_VALUE"]) / 100.0
            dates.add(row["TIME_PERIOD"])
    missing = set(TENORS.values()) - set(points)
    if missing:
        raise ValueError(f"ECB response is missing maturities {sorted(missing)}")
    return max(dates), points


def fetch_curve(timeout: float = 10.0) -> tuple[str, dict[float, float]]:
    resp = requests.get(ECB_URL, timeout=timeout)
    resp.raise_for_status()
    return parse_ecb_csv(resp.text)


def store_curve(conn: sqlite3.Connection, obs_date: str, points: dict[float, float]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO yield_curve VALUES (?, ?, ?)",
        [(obs_date, m, r) for m, r in points.items()],
    )
    conn.commit()


def latest_cached(conn: sqlite3.Connection) -> tuple[str, dict[float, float]] | None:
    row = conn.execute("SELECT MAX(obs_date) FROM yield_curve").fetchone()
    if row is None or row[0] is None:
        return None
    obs_date = row[0]
    points = dict(
        conn.execute(
            "SELECT maturity, rate FROM yield_curve WHERE obs_date = ?", (obs_date,)
        ).fetchall()
    )
    return obs_date, points


def load_curve(conn: sqlite3.Connection, refresh: bool = True, timeout: float = 10.0) -> YieldCurve:
    """Latest curve: the ECB if reachable (and cached), else the cache, else the fallback."""
    if refresh:
        try:
            obs_date, points = fetch_curve(timeout)
            store_curve(conn, obs_date, points)
            return YieldCurve.from_points(obs_date, points, "ecb")
        except (requests.RequestException, ValueError, KeyError):
            pass
    cached = latest_cached(conn)
    if cached is not None:
        return YieldCurve.from_points(*cached, "cache")
    return YieldCurve.from_points(FALLBACK_DATE, FALLBACK_RATES, "fallback")
