import numpy as np
import pytest

from iro import data_ecb, db
from iro.optimizer import Problem
from iro.returns import expected_returns

TOL = 1e-5


@pytest.fixture(scope="module")
def problem(tmp_path_factory):
    conn = db.init_db(tmp_path_factory.mktemp("opt") / "test.db")
    curve = data_ecb.YieldCurve.from_points(
        data_ecb.FALLBACK_DATE, data_ecb.FALLBACK_RATES, "fallback"
    )
    p = Problem(db.load_assets(conn), curve, db.load_assumptions(conn))
    conn.close()
    return p


def check_constraints(p: Problem, w: np.ndarray):
    assert w.sum() == pytest.approx(1.0)
    assert (w >= -TOL).all()
    assert (w <= p.assets["cap"].to_numpy() + TOL).all()
    assert w[p.sub_a].sum() <= p.assumptions["max_bbb_and_below"] + TOL
    assert abs(p.metrics(w)["duration_gap"]) <= p.assumptions["duration_gap_tol"] + 1e-4


# --- Expected returns -----------------------------------------------------------

def test_expected_returns_follow_the_curve(problem):
    mu = dict(zip(problem.assets.index, problem.mu))
    r = problem.curve.rate
    assert mu["GOV_M"] == pytest.approx(r(6.0))
    assert mu["CORP_BBB_S"] == pytest.approx(r(3.0) + (130 - 15) / 1e4)
    assert mu["EQ"] == pytest.approx(r(10.0) + 0.05)


def test_expected_returns_reject_unknown_class(problem):
    assets = problem.assets.copy()
    assets.loc["EQ", "asset_class"] = "crypto"
    with pytest.raises(ValueError, match="unknown asset class"):
        expected_returns(assets, problem.curve, problem.assumptions)


# --- Optimiser ------------------------------------------------------------------

@pytest.mark.parametrize("budget", [30, 45, 60, 80])
def test_solution_respects_all_constraints(problem, budget):
    res = problem.solve(budget)
    assert res.ok, res.status
    check_constraints(problem, res.weights.to_numpy())


@pytest.mark.parametrize("budget", [30, 45, 60, 80])
def test_true_scr_within_budget(problem, budget):
    # The SCR recomputed outside cvxpy with the regulatory rule must respect the budget:
    # this validates the convex formulation against the actual formula.
    res = problem.solve(budget)
    true_scr = problem.model.market(res.weights.to_numpy())["scr_mkt"]
    assert true_scr <= budget + 1e-4
    assert res.metrics["scr_mkt"] == pytest.approx(true_scr)


def test_more_capital_never_lowers_return(problem):
    returns = [problem.solve(b).metrics["exp_return"] for b in (30, 60, 120)]
    assert returns[0] <= returns[1] + 1e-9 <= returns[2] + 2e-9


def test_budget_below_minimum_is_infeasible(problem):
    res = problem.solve(5.0)
    assert res.status == "infeasible"
    assert not res.ok and res.weights is None


def test_min_scr_is_the_feasibility_threshold(problem):
    lo = problem.min_scr()
    assert lo.ok
    assert problem.solve(lo.budget * 1.01).ok
    assert not problem.solve(lo.budget * 0.95).ok


def test_max_return_portfolio_bounds_the_frontier(problem):
    hi = problem.max_return()
    constrained = problem.solve(60)
    assert hi.metrics["exp_return"] >= constrained.metrics["exp_return"] - 1e-9
    check_constraints(problem, hi.weights.to_numpy())


def test_limits_can_be_overridden(problem):
    res = problem.solve(80, max_bbb_and_below=0.0)
    assert res.weights[problem.sub_a].sum() == pytest.approx(0.0, abs=1e-6)


def test_metrics_are_consistent(problem):
    m = problem.solve(60).metrics
    own_funds = problem.A - problem.L
    assert m["solvency_ratio"] == pytest.approx(own_funds / m["scr_mkt"])
    parts = m["scr_int"] + m["scr_eq"] + m["scr_prop"] + m["scr_spread"]
    assert m["divers_benefit"] == pytest.approx(parts - m["scr_mkt"])
