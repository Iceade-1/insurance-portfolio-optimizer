import numpy as np
import pytest

from iro import data_ecb, db
from iro.frontier import efficient_frontier, max_ro_scr, save_frontier
from iro.optimizer import Problem


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    conn = db.init_db(tmp_path_factory.mktemp("frontier") / "test.db")
    curve = data_ecb.YieldCurve.from_points(
        data_ecb.FALLBACK_DATE, data_ecb.FALLBACK_RATES, "fallback"
    )
    p = Problem(db.load_assets(conn), curve, db.load_assumptions(conn))
    yield conn, p, efficient_frontier(p, n=15)
    conn.close()


def test_every_point_is_feasible(setup):
    _, _, f = setup
    assert (f["status"] == "optimal").all()
    assert len(f) == 15


def test_return_non_decreasing_in_budget(setup):
    _, _, f = setup
    assert (np.diff(f["exp_return"]) >= -1e-9).all()


def test_frontier_is_concave(setup):
    # Optimal value of a convex problem as a function of the budget: concave
    _, _, f = setup
    slopes = np.diff(f["exp_return"]) / np.diff(f["budget"])
    assert (np.diff(slopes) <= 1e-6).all()


def test_scr_stays_within_each_budget(setup):
    _, _, f = setup
    assert (f["scr_mkt"] <= f["budget"] * (1 + 1e-6) + 1e-4).all()


def test_endpoints(setup):
    _, p, f = setup
    assert f["budget"].iloc[0] == pytest.approx(p.min_scr().budget, rel=1e-4)
    assert f["exp_return"].iloc[-1] == pytest.approx(p.max_return().metrics["exp_return"], abs=1e-6)


def test_max_ro_scr_point(setup):
    _, _, f = setup
    best = max_ro_scr(f)
    assert best["ro_scr"] == pytest.approx(f["ro_scr"].max())


def test_frontier_saved_as_runs(setup):
    conn, p, f = setup
    ids = save_frontier(conn, f, p.assets.index, {"note": "test"})
    assert len(ids) == len(f)
    runs = db.load_runs(conn)
    assert (runs["mode"] == "frontier").sum() == len(f)
    w = db.load_run_weights(conn, ids[0])
    assert w.sum() == pytest.approx(1.0)
