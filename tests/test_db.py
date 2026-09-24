import pandas as pd
import pytest

from iro import db


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(tmp_path / "test.db")
    yield c
    c.close()


def test_universe_seeded(conn):
    assets = db.load_assets(conn)
    assert len(assets) == 12
    assert list(assets.index[:2]) == ["CASH", "GOV_S"]          # seeding order kept
    assert assets.loc["HY_BB", "cqs"] == 4
    assert assets.loc["EQ", "cap"] == pytest.approx(0.10)
    assert (assets.loc[assets.asset_class == "gov", "spread_bp"] == 0).all()


def test_assumptions_seeded(conn):
    a = db.load_assumptions(conn)
    assert a["assets_mv"] == 1000.0
    assert a["liabilities_be"] == 900.0
    assert a["liab_duration"] == 10.0


def test_init_is_idempotent_and_keeps_edits(tmp_path):
    path = tmp_path / "test.db"
    conn = db.init_db(path)
    db.set_assumption(conn, "liab_duration", 12.0)
    conn.close()

    conn = db.init_db(path)                                     # second init: no duplicates, edit kept
    assert len(db.load_assets(conn)) == 12
    assert db.load_assumptions(conn)["liab_duration"] == 12.0
    conn.close()


def test_save_and_load_run(conn):
    weights = {"GOV_L": 0.6, "CORP_A_M": 0.4}
    run_id = db.save_run(
        conn, "single", {"budget": 60}, weights, {"exp_return": 0.035, "scr_mkt": 55.0}
    )
    runs = db.load_runs(conn)
    assert runs.loc[0, "run_id"] == run_id
    assert runs.loc[0, "scr_mkt"] == pytest.approx(55.0)
    assert pd.isna(runs.loc[0, "scr_eq"])                      # metrics not given stay NULL
    assert db.load_run_weights(conn, run_id).to_dict() == weights


def test_save_run_rejects_unknown_metric(conn):
    with pytest.raises(ValueError, match="unknown metric"):
        db.save_run(conn, "single", {}, {}, {"sharpe": 1.0})
