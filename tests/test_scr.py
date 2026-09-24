import numpy as np
import pytest

from iro import data_ecb, db, scr


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    conn = db.init_db(tmp_path_factory.mktemp("scr") / "test.db")
    assets = db.load_assets(conn)
    assumptions = db.load_assumptions(conn)
    curve = data_ecb.YieldCurve.from_points(
        data_ecb.FALLBACK_DATE, data_ecb.FALLBACK_RATES, "fallback"
    )
    conn.close()
    return assets, assumptions, curve, scr.ScrModel.build(assets, curve, assumptions)


def weights(assets, **alloc):
    w = np.zeros(len(assets))
    for asset_id, x in alloc.items():
        w[assets.index.get_loc(asset_id)] = x
    return w


# --- Spread factor, Art. 176(3) ----------------------------------------------

@pytest.mark.parametrize("duration, cqs, expected", [
    (3, 3, 0.075),        # BBB, d <= 5: 2.5 % x 3
    (7, 2, 0.084),        # A, 5 < d <= 10: 7.0 % + 0.7 % x 2
    (5, 1, 0.055),        # AA at the band edge: 1.1 % x 5 (d = 5 is in the first band)
    (12, 1, 0.094),       # AA, 10 < d <= 15: 8.4 % + 0.5 % x 2 (official 8.4 %, not 8.5 %)
    (18, 1, 0.124),       # AA, 15 < d <= 20: 10.9 % + 0.5 % x 3
    (25, 1, 0.159),       # AA, d > 20: 13.4 % + 0.5 % x 5
    (4, 4, 0.18),         # BB: 4.5 % x 4
    (0.5, 0, 0.009),      # duration floored at 1 year (Art. 176(2))
])
def test_spread_factor(duration, cqs, expected):
    assert scr.spread_factor(duration, cqs) == pytest.approx(expected)


def test_spread_factor_capped_at_100_percent():
    assert scr.spread_factor(200, 5) == 1.0


# --- Interest rate shocks, Art. 166-167 --------------------------------------

@pytest.mark.parametrize("m, up, down", [
    (0.25, 0.70, 0.75),   # below 1y: flat at the 1y value
    (1, 0.70, 0.75),
    (6, 0.52, 0.42),
    (16, 0.31, 0.28),
    (90, 0.20, 0.20),
    (120, 0.20, 0.20),    # beyond 90y: flat
    (55, 0.23, 0.245),    # linear between 20y and 90y
])
def test_ir_shocks(m, up, down):
    assert scr.ir_shock_up(m) == pytest.approx(up)
    assert scr.ir_shock_down(m) == pytest.approx(down)


def test_rate_up_at_least_one_point_and_no_down_shock_when_negative():
    curve = data_ecb.YieldCurve.from_points("d", {1.0: -0.005, 20.0: 0.03}, "test")
    up20, _ = scr.rate_moves(curve, 20.0)
    assert up20 == pytest.approx(0.01)          # 3 % x 26 % = 0.78 pp, floored at 1 pp
    _, down1 = scr.rate_moves(curve, 1.0)
    assert down1 == 0.0                          # negative rate: decrease is nil


# --- Correlations, Art. 164(3) ------------------------------------------------

@pytest.mark.parametrize("corr", [scr.C_UP, scr.C_DOWN])
def test_correlation_matrices_positive_definite(corr):
    assert np.allclose(corr, corr.T)
    np.linalg.cholesky(corr)                     # raises if not positive definite


# --- Model ------------------------------------------------------------------

def test_equity_only_portfolio(setup):
    assets, assumptions, _, model = setup
    c = model.components(weights(assets, EQ=1.0))
    assert c["equity"] == pytest.approx(0.39 * assumptions["assets_mv"])
    assert c["spread"] == 0.0 and c["property"] == 0.0


def test_govies_carry_no_spread_charge(setup):
    assets, *_, model = setup
    c = model.components(weights(assets, GOV_S=0.3, GOV_M=0.3, GOV_L=0.4))
    assert c["spread"] == 0.0


def test_spread_charge_uses_bucket_factor(setup):
    assets, assumptions, _, model = setup
    c = model.components(weights(assets, CORP_BBB_S=1.0))
    assert c["spread"] == pytest.approx(0.075 * assumptions["assets_mv"])


def test_short_assets_lose_when_rates_fall(setup):
    # Cash against 10y liabilities: liabilities rise when rates fall
    assets, *_, model = setup
    c = model.components(weights(assets, CASH=1.0))
    assert c["int_down"] > 0 and c["int_up"] == 0.0
    assert model.market(weights(assets, CASH=1.0))["scenario"] == "down"


def test_long_assets_lose_when_rates_rise(setup):
    assets, *_, model = setup
    c = model.components(weights(assets, GOV_L=1.0))
    assert c["int_up"] > 0 and c["int_down"] == 0.0


def test_duration_matching_reduces_interest_charge(setup):
    assets, assumptions, _, model = setup
    # Dollar-duration matching: A * D_A = L * D_L, i.e. D_A = 900 * 10 / 1000 = 9 years,
    # reached by mixing GOV_M (6y) and GOV_L (15y)
    target = assumptions["liabilities_be"] * assumptions["liab_duration"] / assumptions["assets_mv"]
    x = (target - 6.0) / (15.0 - 6.0)
    matched = weights(assets, GOV_M=1 - x, GOV_L=x)
    cash = weights(assets, CASH=1.0)
    c_matched, c_cash = model.components(matched), model.components(cash)
    assert max(c_matched["int_up"], c_matched["int_down"]) < 0.25 * c_cash["int_down"]


def test_aggregation_follows_selected_scenario(setup):
    assets, *_, model = setup
    w = weights(assets, GOV_L=0.5, EQ=0.1, CORP_A_M=0.4)
    m = model.market(w)
    s = np.array(list(m["charges"].values()))
    corr = scr.C_UP if m["scenario"] == "up" else scr.C_DOWN
    assert m["scr_mkt"] == pytest.approx(np.sqrt(s @ corr @ s))


def test_diversification_non_negative(setup):
    assets, *_, model = setup
    rng = np.random.default_rng(0)
    for _ in range(200):
        w = rng.dirichlet(np.ones(len(assets)))
        m = model.market(w)
        assert m["diversification"] >= -1e-9
        assert m["scr_mkt"] <= sum(m["charges"].values()) + 1e-9


def test_bucket_allocation_sums_to_scr(setup):
    assets, *_, model = setup
    rng = np.random.default_rng(1)
    for _ in range(50):
        w = rng.dirichlet(np.ones(len(assets)))
        table = model.by_bucket(w)
        assert table.to_numpy().sum() == pytest.approx(model.market(w)["scr_mkt"])
        assert "Liabilities" in table.index
