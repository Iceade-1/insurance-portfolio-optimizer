import pytest
import requests

from iro import data_ecb, db

SAMPLE_CSV = "KEY,DATA_TYPE_FM,TIME_PERIOD,OBS_VALUE\n" + "\n".join(
    f"YC.x.{tenor},{tenor},2026-09-23,{rate * 100}"
    for tenor, rate in zip(data_ecb.TENORS, data_ecb.FALLBACK_RATES.values())
)


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture
def offline(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(data_ecb.requests, "get", fail)


def test_parse_ecb_csv():
    obs_date, points = data_ecb.parse_ecb_csv(SAMPLE_CSV)
    assert obs_date == "2026-09-23"
    assert len(points) == 10
    assert points[10.0] == pytest.approx(0.035237)             # percent -> decimal


def test_parse_rejects_incomplete_response():
    truncated = "\n".join(SAMPLE_CSV.splitlines()[:-1])
    with pytest.raises(ValueError, match="missing maturities"):
        data_ecb.parse_ecb_csv(truncated)


def test_interpolation_and_flat_extrapolation():
    curve = data_ecb.YieldCurve.from_points("d", data_ecb.FALLBACK_RATES, "fallback")
    r5, r6, r7 = curve.rate(5), curve.rate(6), curve.rate(7)
    assert r6 == pytest.approx((r5 + r7) / 2)                   # linear between nodes
    assert curve.rate(0.0) == pytest.approx(curve.rate(0.25))   # flat below 3M
    assert curve.rate(50.0) == pytest.approx(curve.rate(30.0))  # flat beyond 30Y
    assert curve.rate([2.0, 10.0]).shape == (2,)                # vectorised


def test_offline_without_cache_uses_fallback(conn, offline):
    curve = data_ecb.load_curve(conn)
    assert curve.source == "fallback"
    assert curve.obs_date == data_ecb.FALLBACK_DATE


def test_offline_with_cache_uses_cache(conn, offline):
    data_ecb.store_curve(conn, "2026-09-01", {1.0: 0.03, 10.0: 0.035})
    curve = data_ecb.load_curve(conn)
    assert curve.source == "cache"
    assert curve.obs_date == "2026-09-01"
    assert curve.rate(10) == pytest.approx(0.035)


@pytest.mark.network
def test_live_ecb_fetch(conn):
    try:
        curve = data_ecb.load_curve(conn, timeout=20)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"ECB unreachable: {exc}")
    if curve.source != "ecb":
        pytest.skip("ECB unreachable")
    rows = conn.execute(
        "SELECT COUNT(*) FROM yield_curve WHERE obs_date = ?", (curve.obs_date,)
    ).fetchone()[0]
    assert rows == 10
    r5, r6, r7 = curve.rate([5, 6, 7])
    assert min(r5, r7) <= r6 <= max(r5, r7)
    assert -0.02 < r6 < 0.10                                    # plausible euro rate
