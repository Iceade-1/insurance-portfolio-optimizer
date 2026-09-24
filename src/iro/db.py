"""SQLite persistence: investment universe, assumptions and optimisation runs."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import pandas as pd

# id, name, asset_class, cqs, duration, spread_bp, el_bp, cap
# Spreads and expected losses are illustrative assumptions, editable in the app.
ASSETS = [
    ("CASH",       "Cash / money market",      "cash",     None, 0.25,   0,  0, 0.30),
    ("GOV_S",      "EUR govies 1-3y",          "gov",      0,    2.0,    0,  0, 0.30),
    ("GOV_M",      "EUR govies 5-7y",          "gov",      0,    6.0,    0,  0, 0.30),
    ("GOV_L",      "EUR govies 10y+",          "gov",      0,    15.0,   0,  0, 0.30),
    ("CORP_AA",    "Corporates AA",            "corp",     1,    5.0,   50,  2, 0.30),
    ("CORP_A_S",   "Corporates A short",       "corp",     2,    3.0,   80,  5, 0.30),
    ("CORP_A_M",   "Corporates A medium",      "corp",     2,    7.0,  100,  6, 0.30),
    ("CORP_BBB_S", "Corporates BBB short",     "corp",     3,    3.0,  130, 15, 0.30),
    ("CORP_BBB_M", "Corporates BBB medium",    "corp",     3,    6.0,  160, 18, 0.30),
    ("HY_BB",      "High yield BB",            "corp",     4,    4.0,  300, 90, 0.05),
    ("EQ",         "Equity type 1",            "equity",   None, 0.0,    0,  0, 0.10),
    ("RE",         "Property",                 "property", None, 0.0,    0,  0, 0.10),
]

ASSUMPTIONS = {
    "assets_mv": 1000.0,          # A, EUR m
    "liabilities_be": 900.0,      # L, best-estimate liabilities, EUR m
    "liab_duration": 10.0,        # D_L
    "equity_risk_premium": 0.05,
    "property_premium": 0.03,
    "duration_gap_tol": 0.5,      # tau: max |A*D_A - L*D_L| / A
    "max_bbb_and_below": 0.30,    # combined cap on BBB and HY buckets
}

RUN_METRIC_COLUMNS = (
    "exp_return", "scr_int", "scr_eq", "scr_prop", "scr_spread", "scr_mkt",
    "divers_benefit", "solvency_ratio", "ro_scr", "duration_gap",
)


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str | Path, seed: bool = True) -> sqlite3.Connection:
    """Create the schema if needed and seed the universe and assumptions (idempotent)."""
    conn = connect(path)
    schema = resources.files("iro").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    if seed:
        conn.executemany("INSERT OR IGNORE INTO assets VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ASSETS)
        conn.executemany(
            "INSERT OR IGNORE INTO assumptions VALUES (?, ?)", ASSUMPTIONS.items()
        )
    conn.commit()
    return conn


def load_assets(conn: sqlite3.Connection) -> pd.DataFrame:
    """Universe in seeding order, indexed by asset id."""
    return pd.read_sql_query(
        "SELECT * FROM assets ORDER BY rowid", conn, index_col="id"
    )


def load_assumptions(conn: sqlite3.Connection) -> dict[str, float]:
    return dict(conn.execute("SELECT key, value FROM assumptions").fetchall())


def set_assumption(conn: sqlite3.Connection, key: str, value: float) -> None:
    conn.execute(
        "INSERT INTO assumptions VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, float(value)),
    )
    conn.commit()


def save_run(
    conn: sqlite3.Connection,
    mode: str,
    params: dict,
    weights: dict[str, float],
    metrics: dict[str, float],
) -> int:
    """Persist one optimisation run and return its id."""
    unknown = set(metrics) - set(RUN_METRIC_COLUMNS)
    if unknown:
        raise ValueError(f"unknown metric(s): {sorted(unknown)}")

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO runs (created_at, mode, params_json) VALUES (?, ?, ?)",
        (created_at, mode, json.dumps(params, sort_keys=True)),
    )
    run_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO run_weights VALUES (?, ?, ?)",
        [(run_id, asset_id, float(w)) for asset_id, w in weights.items()],
    )
    cols = ", ".join(metrics)
    marks = ", ".join("?" for _ in metrics)
    conn.execute(
        f"INSERT INTO run_metrics (run_id, {cols}) VALUES (?, {marks})",
        (run_id, *map(float, metrics.values())),
    )
    conn.commit()
    return run_id


def load_runs(conn: sqlite3.Connection) -> pd.DataFrame:
    """All runs with their metrics, most recent first."""
    metric_cols = ", ".join(f"m.{c}" for c in RUN_METRIC_COLUMNS)
    return pd.read_sql_query(
        f"""
        SELECT r.run_id, r.created_at, r.mode, r.params_json, {metric_cols}
        FROM runs r
        LEFT JOIN run_metrics m USING (run_id)
        ORDER BY r.run_id DESC
        """,
        conn,
    )


def load_run_weights(conn: sqlite3.Connection, run_id: int) -> pd.Series:
    return pd.read_sql_query(
        "SELECT asset_id, weight FROM run_weights WHERE run_id = ?",
        conn,
        params=(run_id,),
        index_col="asset_id",
    )["weight"]
