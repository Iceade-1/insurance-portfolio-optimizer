# Roadmap: Solvency II-aware insurance portfolio optimiser

**Why this repo exists:** it's the project listed on the AllianzGI *Intern Quantitative
Developer* CV (offer ID 106246, Insurance & Regulatory Strategies team). **The application is sent
only once this roadmap is finished.**

- Full specification (formulas, regulatory tables, SQL schema):
  `C:\Users\adamj\Desktop\projects\URGENT - Intern Quantitative Developer (AllianzGI Paris).md`
- CV to send afterwards: `C:\Users\adamj\Desktop\projects\CV_JAOUDAR_ADAM_QuantDev_AllianzGI.tex`
- Self-imposed deadline (from the tracker): **Saturday evening**. If it isn't finished, send the
  CV without the project rather than miss the offer.

**Total effort: ~8 h**, in 6 phases. Each phase ends with a **checkpoint** that has to pass
before moving on.

---

## Architecture at a glance

```
ECB API ─► data_ecb.py ─► SQLite (yield_curve, assets, assumptions)
                                    │
                                    ▼
                returns.py (μ per bucket)   scr.py (SCR by sub-module, aggregation)
                                    │             │
                                    └──► optimizer.py (cvxpy SOCP) ◄──┘
                                              │
                               frontier.py (budget sweep, RoSCR)
                                              │
                          SQLite (runs, run_weights, run_metrics)
                                              │
                                        app.py (Streamlit, 4 tabs)
```

---

## Phase 0: Setup (30 min) ✅ done 2026-09-24

- [x] `git init` in this folder, add a Python `.gitignore` (+ `*.db`, `.venv/`)
- [x] Virtual environment:
  ```powershell
  python -m venv .venv            # Python 3.14 (cvxpy 1.9.3 has a cp314 wheel)
  # fallback if an install fails: py -3.12 -m venv .venv
  .venv\Scripts\Activate.ps1
  pip install numpy pandas scipy cvxpy clarabel streamlit plotly requests pytest
  pip freeze > requirements.txt
  ```
- [x] Layout: `src/iro/` (package), `tests/`, `app.py`, `schema.sql`, `README.md`
- [x] Empty `src/iro/__init__.py`, and a `pyproject.toml` or `pip install -e .` so tests can import `iro`

**✅ Checkpoint 0 (passed: CLARABEL present, 2 tests green):** `python -c "import cvxpy; print(cvxpy.installed_solvers())"` lists `CLARABEL`,
and `pytest` runs (0 tests, no error).

---

## Phase 1: Data (1 h) ✅ done 2026-09-24

**`src/iro/schema.sql` (shipped inside the package) + `src/iro/db.py`**
- [x] 6 tables: `assets`, `yield_curve`, `assumptions`, `runs`, `run_weights`, `run_metrics` (spec §2.6)
- [x] `init_db(path)`: creates the tables, seeds the 12 buckets (spec §2.2) and the assumptions
  (A = 1000, L = 900, D_L = 10, ERP = 5%, property premium = 3%, τ = 0.5, caps)
- [x] `save_run(...)` / `load_runs()`

**`src/iro/data_ecb.py`**
- [x] `fetch_curve()`: ECB AAA spot curve, maturities 3M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y, 15Y, 20Y, 30Y
  (URL in spec §2.7), inserted into `yield_curve`
- [x] **Hard-coded fallback curve** if the API fails (needed for a live interview demo)
- [x] `rate(m)`: linear interpolation, flat extrapolation beyond 30Y

**✅ Checkpoint 1 (passed: live ECB curve of 2026-09-23, 10 rows, r(6) = 3.363% between r(5) = 3.330% and r(7) = 3.396%; 13 tests green):** `init_db` + `fetch_curve` run, `SELECT * FROM yield_curve` returns
10 rows, and `rate(6)` gives a plausible rate (between r(5) and r(7)).

---

## Phase 2: SCR engine (1 h 30), the core of the project ✅ done 2026-09-24

**`src/iro/scr.py`**
- [x] `spread_factor(duration, cqs)`: Art. 176 table (spec §2.4), capped at 100%, 0 for EEA govies
- [x] `ir_shocks(m)`: relative up/down shocks, interpolated; `Δy_up = max(r·s_up, 1%)`,
  `Δy_down = r·s_down` if r > 0, else 0
- [x] `scr_components(w)`: returns `{int_up, int_down, eq, prop, spread}` for a given weight vector
- [x] `C_UP`, `C_DOWN`: 4×4 correlation matrices (order IR, EQ, PROP, SPREAD)
- [x] `scr_market(w)`: aggregation in both scenarios, keeping the one whose interest charge
  is the larger (Art. 164), plus the diversification benefit
- [x] `scr_by_bucket(w)`: contribution of each bucket, for the chart

**`tests/test_scr.py`** (write the tests **alongside** the code)
- [x] `spread_factor(3, 3) == 0.075` (BBB, d=3 → 2.5%×3)
- [x] `spread_factor(7, 2) == 0.084` (A, d=7 → 7% + 0.7%×2)
- [x] Duration floored at 1 (`spread_factor(0.5, 0) == 0.009`); govies → 0 via asset class (Art. 180(2))
- [x] `C_UP` and `C_DOWN` are positive definite (`np.linalg.cholesky` doesn't raise)
- [x] A 100% equity portfolio → `eq == 0.39 × A`
- [x] Diversification benefit ≥ 0

**✅ Checked against the Official Journal PDF (3 AA values corrected: 8.4 / 10.9 / 13.4 %).** Originally: compare the spread table and the rate shocks against the text of
Delegated Regulation (EU) 2015/35 (Art. 164, 166, 167, 176). They were written from memory.
Keep a note in the README of what was checked.

**✅ Checkpoint 2 (passed: 41 tests green; typical insurer mix → SCR_mkt 67, solvency 149 %):** `pytest tests/test_scr.py` is green, and you can explain each formula out
loud without notes.

---

## Phase 3: Returns and optimiser (1 h 30) ✅ done 2026-09-24

**`src/iro/returns.py`**
- [x] μ per bucket (spec §2.3): govies = r(D); corporates = r(D) + spread − PD×LGD;
  equity = r(10) + ERP; property = r(10) + 3%; cash = r(0.25)

**`src/iro/optimizer.py`**
- [x] `solve(budget, constraints) -> Result(weights, exp_return, scr, status)`
- [x] Variables `w ≥ 0`, `Σw = 1`, caps per bucket, BBB+HY ≤ 30%, HY ≤ 5%, EQ ≤ 10%, RE ≤ 10%
- [x] Duration gap: `|A·Σ wᵢDᵢ − L·D_L| / A ≤ τ`
- [x] Auxiliary variables `t_up ≥ loss_up(w)`, `t_up ≥ 0` (same for down)
- [x] Two SOC constraints: `‖chol(C)ᵀ · s‖₂ ≤ B`, one per scenario
- [x] Solver: `cp.CLARABEL`; handle `infeasible` cleanly (budget too low)
- [x] **After solving:** recompute the SCR with `scr.py` (outside cvxpy) and check it's ≤ B.
  This validates that the convex formulation matches the real formula.

**`tests/test_optimizer.py`**
- [x] Weights sum to 1, all ≥ 0, every cap respected
- [x] Recomputed SCR(w*) ≤ B + 1e-6
- [x] Budget ×2 → expected return ≥ (never worse)
- [x] Very low budget → `status == infeasible`, no crash

**✅ Checkpoint 3 (passed: B = 60 → 54 % govies, 38 % A/BBB, 6 % equity, 2 % property, return 4.25 %, solved in 12 ms; min SCR 23.5, unconstrained SCR 94; 57 tests green):** a solve at B = 60 returns a sensible portfolio (mostly govies and
IG, duration close to the liabilities'), with every test green.

---

## Phase 4: Frontier and return on capital (45 min)

**`src/iro/frontier.py`**
- [ ] `efficient_frontier(b_min, b_max, n=25)`: budget sweep, then a table of (B, return, SCR, weights)
- [ ] **RoSCR** = `(μᵀw·A − r(D_L)·L) / SCR_mkt`. That's the excess return over the cost of the
  liabilities, per euro of capital. Record this choice in the README.
- [ ] Mark the max-RoSCR point
- [ ] Save every run in the `runs` / `run_weights` / `run_metrics` tables

**`tests/test_frontier.py`**
- [ ] Return is non-decreasing as the budget grows

**✅ Checkpoint 4:** the frontier plots (matplotlib is fine for checking) and bends
concavely.

---

## Phase 5: Streamlit app (1 h 30)

**`app.py`: 4 tabs**
1. [ ] **Inputs:** balance sheet (A, L, D_L), SCR budget slider, constraint limits, editable
   assumptions table (`st.data_editor`), "Refresh ECB curve" button
2. [ ] **Optimal portfolio:** weights bar chart plus KPIs (expected return, SCR_mkt, solvency
   ratio, duration gap, RoSCR)
3. [ ] **Efficient frontier:** Plotly chart of return vs SCR, with the current point and the max-RoSCR point
4. [ ] **Capital decomposition:** waterfall (IR + EQ + PROP + SPREAD → −diversification → SCR_mkt)
   plus SCR by bucket
- [ ] `@st.cache_data` on the curve loading

**✅ Checkpoint 5:** `streamlit run app.py` works from a fresh clone, and moving the slider
updates every tab with no errors.

---

## Phase 6: README, publication, application (45 min)

- [ ] **README.md**: the business problem (one paragraph); the model (formulas); why it's
  convex (SOCP); **simplifications** (standard formula v1 = 2015 calibration, no symmetric
  adjustment, a single liability duration, spreads/PDs are assumptions); **what was checked**
  against the regulation; screenshots of the 4 tabs; how to run it; next steps (the Solvency II
  review, liability cash flows, ESG constraints, ex-post VaR)
- [ ] Final `pytest` green, `pip freeze > requirements.txt`
- [ ] Clean commits, then create a **public** GitHub repo `insurance-portfolio-optimizer`, then push
- [ ] In the CV `.tex`: uncomment the GitHub line (CONTACT block) and recompile with `xelatex`
- [ ] Go through the **"CV contract"** table (spec §0) and tick every line
- [ ] **Apply** on the Allianz portal (English CV, tick the consent box for AI support)
- [ ] Update `Desktop\projects\CLAUDE.md` (paused task 1 → sent)

**✅ Checkpoint 6 (final):** every claim in the CV's project block can be demonstrated live in
under 2 minutes.

---

## Interview prep (in parallel, evenings)

- [ ] Solvency II: 3 pillars, SCR = 99.5% VaR over 1 year, standard formula vs **internal model**
  (Allianz uses one), BSCR, MCR, solvency ratio
- [ ] Fixed income: modified duration, convexity, DV01, spread vs OAS, rating → CQS → capital charge, ALM
- [ ] Explain in 30 s why the problem is convex (non-negative affine charges, non-negative
  correlations, so the aggregation is a norm, so second-order cone constraints)
- [ ] SQL: joins, window functions (rank the runs by RoSCR)
- [ ] 30 min using Microsoft Copilot (listed in the offer)

---

## Stretch goals (NOT on the CV until they're built)

- Ex-post historical VaR/ES of the optimal portfolio (ETF proxies via yfinance)
- ESG exclusion per bucket
- Liability cash-flow ladder instead of a single duration
- Solvency II review shift+relative rate method
