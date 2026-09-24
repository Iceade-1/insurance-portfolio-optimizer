import cvxpy as cp

import iro


def test_package_importable():
    assert iro.__version__ == "0.1.0"


def test_socp_solver_available():
    assert "CLARABEL" in cp.installed_solvers()
