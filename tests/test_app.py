from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("IRO_DB", str(tmp_path / "app.db"))
    monkeypatch.setenv("IRO_OFFLINE", "1")
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    return at


def budget_slider(at):
    return next(s for s in at.sidebar.slider if s.label.startswith("SCR market budget"))


def test_app_runs_without_errors(app):
    assert not app.exception
    assert not app.error
    assert [t.label for t in app.tabs] == ["Inputs", "Optimal portfolio", "Efficient frontier", "Capital decomposition"]


def test_kpis_rendered(app):
    labels = [m.label for m in app.metric]
    assert "SCR market" in labels and "Return on SCR" in labels


def test_moving_budget_updates_scr(app):
    before = next(m.value for m in app.metric if m.label == "SCR market")
    slider = budget_slider(app)
    slider.set_value(slider.min + 1.0).run()
    after = next(m.value for m in app.metric if m.label == "SCR market")
    assert not app.exception
    assert after != before


def test_save_run(app):
    save = next(b for b in app.button if b.label == "Save this run")
    save.click().run()
    assert not app.exception
    assert any("Saved as run" in s.value for s in app.success)
