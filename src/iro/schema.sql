-- Investment universe: one row per asset bucket.
CREATE TABLE IF NOT EXISTS assets (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    asset_class  TEXT NOT NULL CHECK (asset_class IN ('cash', 'gov', 'corp', 'equity', 'property')),
    cqs          INTEGER,            -- credit quality step (0 = AAA ... 4 = BB); NULL if not a bond
    duration     REAL NOT NULL,      -- modified duration in years (0 for equity / property)
    spread_bp    REAL NOT NULL,      -- credit spread over the AAA curve, in bp
    el_bp        REAL NOT NULL,      -- expected loss (PD x LGD), in bp per year
    cap          REAL NOT NULL       -- maximum portfolio weight
);

-- AAA euro-area spot curve (ECB), rates as decimals.
CREATE TABLE IF NOT EXISTS yield_curve (
    obs_date  TEXT NOT NULL,
    maturity  REAL NOT NULL,         -- years
    rate      REAL NOT NULL,
    PRIMARY KEY (obs_date, maturity)
);

-- Scalar model assumptions (balance sheet, premia, limits).
CREATE TABLE IF NOT EXISTS assumptions (
    key    TEXT PRIMARY KEY,
    value  REAL NOT NULL
);

-- One row per optimisation run.
CREATE TABLE IF NOT EXISTS runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    mode         TEXT NOT NULL,      -- 'single' or 'frontier'
    params_json  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_weights (
    run_id    INTEGER NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    asset_id  TEXT NOT NULL REFERENCES assets (id),
    weight    REAL NOT NULL,
    PRIMARY KEY (run_id, asset_id)
);

CREATE TABLE IF NOT EXISTS run_metrics (
    run_id          INTEGER PRIMARY KEY REFERENCES runs (run_id) ON DELETE CASCADE,
    exp_return      REAL,
    scr_int         REAL,
    scr_eq          REAL,
    scr_prop        REAL,
    scr_spread      REAL,
    scr_mkt         REAL,
    divers_benefit  REAL,
    solvency_ratio  REAL,
    ro_scr          REAL,
    duration_gap    REAL
);
