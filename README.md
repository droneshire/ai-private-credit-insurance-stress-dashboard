# AI / Private Credit / Insurance Stress Dashboard

A Jupyter notebook for monitoring the thesis:

> AI and data-center economics weaken, structured/private credit deteriorates, insurer capital pressure rises, funding stress appears, and policy response follows.

The dashboard keeps observable evidence separate from the broader thesis. A deterioration in one layer does not imply insurer insolvency.

## Monitoring layers

1. Compute economics
2. Credit markets
3. Private credit
4. Insurance balance sheets
5. Insurance funding and liquidity
6. Policy response

## Data sources

The notebook automatically downloads public series from FRED. It creates CSV templates in `stress_dashboard_data/` for inputs that lack dependable free real-time APIs:

- Data-center ABS and structured-credit rating actions
- BDC PIK income, non-accruals, and NAV changes
- NAIC statutory insurer metrics and RBC
- Bermuda reinsurance and BSCR metrics

## Setup

Install [uv](https://docs.astral.sh/uv/), then run:

    uv sync --group dev
    uv run jupyter lab

Open `ai_private_credit_insurance_stress_dashboard.ipynb` and run all cells.

To execute it non-interactively:

    uv run jupyter nbconvert \
      --to notebook \
      --execute ai_private_credit_insurance_stress_dashboard.ipynb \
      --output /tmp/ai_stress_dashboard.executed.ipynb \
      --ExecutePreprocessor.timeout=180

## Development checks

    uv run python scripts/validate_notebook.py
    uv run mypy scripts

The validator checks the notebook schema and compiles every code cell without running network requests.

## Important limitation

The scores are transparent monitoring heuristics, not forecasts, investment advice, or estimates of crisis probability. File-driven layers remain `NO DATA` until their CSV inputs are populated.
