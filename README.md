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
