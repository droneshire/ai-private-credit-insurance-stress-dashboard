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

Collected automatically:

| Layer | Source | Collector |
|---|---|---|
| 1 Compute economics | SEC XBRL: TTM capex of Microsoft, Alphabet, Amazon, Meta, Oracle, CoreWeave; TTM revenue of NVIDIA, CoreWeave. Vast.ai on-demand GPU prices | `scripts/fetch_compute.py` |
| 2 Credit markets | FRED: Treasury yield, IG / HY OAS | notebook |
| 3 Private credit | SEC XBRL and earnings releases for ARCC, MAIN, OBDC, FSK | `scripts/fetch_bdc_sec.py` |
| 4 Insurance balance sheets | SEC XBRL, XBRL instances and earnings releases for Athene, Corebridge, F&G, Jackson, Lincoln, Brighthouse (GAAP proxy) | `scripts/fetch_insurers_sec.py` |
| 5 Funding / liquidity | FRED: Fed primary credit; Z.1 life-insurer FABS liabilities | notebook |
| 6 Policy response | FRED: Fed total assets, primary credit | notebook |

Still entered by hand in `stress_dashboard_data/` (templates are created on first run):

- `rating_actions.csv`: data-center ABS / structured-credit rating actions (no free machine-readable feed)
- `insurer_statutory.csv`: NAIC statutory metrics; when complete it replaces the GAAP proxy
- `bermuda_reinsurance.csv`: BMA / BSCR metrics (annual PDFs)
- Any BDC field SEC filings leave blank in `bdc_metrics.csv`; hand entries survive later collector runs

### What the collectors extract

**BDCs** (`bdc_metrics.csv`, last four quarters, each row citing the original filing):

- `nav_per_share`, `nav_qoq_pct` from `NetAssetValuePerShare`
- `pik_income_pct` = `InterestIncomeOperatingPaidInKind` / `GrossInvestmentIncomeOperating` (MAIN does not tag PIK)
- `realized_loss_pct`: the quarter's net realized loss as % of the portfolio at fair value
- `interest_coverage` = (net investment income + interest expense) / interest expense
- `non_accrual_pct` (at amortized cost) and `non_accrual_fv_pct` (at fair value), parsed from the
  earnings release; the quoted sentence and URL are in `bdc_non_accrual_evidence.csv`

Quarters are derived from year-to-date facts when only those are tagged. Where issuers use different
tags for the same concept, an ordered fallback list is used and the tag is recorded in `notes`.

**Insurers** (`insurer_sec_gaap.csv`, same columns as the statutory template plus `aoci_usd_m`):
GAAP stockholders' equity in place of capital & surplus, AOCI, total assets, gross unrealized losses on
AFS debt, reinsurance recoverables, recurring Level 3 assets (read from the latest XBRL instance, since
the figure is dimensional), and the RBC ratio quoted from the earnings release (`insurer_rbc_evidence.csv`;
Athene, Corebridge and F&G do not state a figure). NAIC designation migration is not disclosed in GAAP
filings.

**Compute** (`compute_sec.csv`, `gpu_price_history.csv`): trailing-twelve-month sums of four derived
quarters, so different fiscal calendars compare cleanly. Each run appends a GPU price snapshot; CI
restores the history from the last successful `main` run, so the price trend is scored once 60+ days
have accumulated.

Nothing is inferred: a figure that is not tagged, or not stated in a recognised phrasing, stays blank
and the coverage files (`*_coverage.csv`) say why.

### Scoring

The notebook documents each layer's formula in section 7, and the `basis` column of
`latest_stress_snapshot.csv` records which inputs produced each score. GAAP equity is not statutory
surplus, so the insurer proxy is scored on RBC shortfall below 400%, the year-over-year change in equity
excluding AOCI, and unrealized losses relative to equity; Level 3 / equity is shown for context only.

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

To refresh the SEC and GPU inputs first:

    export SEC_USER_AGENT="YourApp/1.0 you@example.com"
    uv run python scripts/fetch_bdc_sec.py
    uv run python scripts/fetch_insurers_sec.py
    uv run python scripts/fetch_compute.py

SEC's fair-access policy requires a descriptive identity with a contact email; it is sent to SEC
hosts only.

## Continuous integration

CI runs on every push and pull request, weekly on Mondays, and on demand (`workflow_dispatch`). Set
the repository Actions variable `SEC_USER_AGENT` to enable the collectors. The
`stress-dashboard-results` artifact contains the executed notebook, `stress_dashboard_report.html` and
every CSV.

Runs on `main` also publish the report to GitHub Pages:
https://droneshire.github.io/ai-private-credit-insurance-stress-dashboard/ always shows the latest
report, and `archive.html` lists dated copies, which are deleted after 14 days. The `gh-pages` branch is
rewritten as a single commit on each publish, so expired reports are not kept in its history.

## Development checks

    uv run python scripts/validate_notebook.py
    uv run mypy scripts
    uv run pytest -q

The validator checks the notebook schema and compiles every code cell without running network requests.
The tests cover the release-text parsers against the real phrasings, XBRL quarter derivation and the
manual-entry merge.

## Important limitation

The scores are transparent monitoring heuristics, not forecasts, investment advice, or estimates of crisis probability. A layer shows `NO DATA` when its inputs are missing or too sparse to score; a missing layer is not low risk. The insurer layer uses a GAAP proxy unless statutory data is supplied.
