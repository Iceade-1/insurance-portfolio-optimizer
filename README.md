# Insurance portfolio optimiser (Solvency II)

Portfolio construction for an insurer's balance sheet: maximise expected return under a
Solvency II market-risk SCR budget, with duration matching against liabilities and rating /
concentration limits, formulated as a convex second-order cone program.

> Work in progress: see `ROADMAP.md`.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest
```

## Regulatory parameters: what was checked

All parameters were checked against the text of **Commission Delegated Regulation (EU) 2015/35
as adopted** (OJ L 12, 17.1.2015), using the Official Journal PDF:

| Parameter | Article | Status |
|---|---|---|
| Spread risk factors by CQS and duration band, `min[...; 1]` cap above 20y | 176(3) | ✅ checked (AA row: 8.4 %, 10.9 %, 13.4 %) |
| Duration floored at 1 year for spread risk | 176(2) | ✅ checked |
| 0 % spread charge on EEA central governments in domestic currency | 180(2) | ✅ checked |
| Relative rate shocks 1–20y and 90y, linear interpolation, flat outside | 166(1), 167(1) | ✅ checked (full tables) |
| Rate increase of at least 1 percentage point | 166(2) | ✅ checked |
| No decrease for negative rates | 167(2) | ✅ checked |
| Interest charge = larger of the up / down scenarios | 165(1) | ✅ checked |
| Market correlation matrix, A = 0 (up scenario) or 0.5 (otherwise) | 164(3) | ✅ checked |
| Equity type 1: 39 % (symmetric adjustment not modelled) | 169(1)(b) | ✅ checked |
| Property: 25 % | 174 | ✅ checked |

Not yet reflected: later amendments (Delegated Regulation (EU) 2019/981 and the Solvency II
review), notably the new interest-rate method. This model is the 2015 standard formula.
