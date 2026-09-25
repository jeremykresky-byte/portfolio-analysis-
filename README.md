# Portfolio analysis: retirement forecast & trend scanner

A small, dependency-free Python tool (standard library only, Python 3.10+) that:

1. **Forecasts retirement** with a Monte Carlo simulation. It compares your *current* holdings against a
   *recommended* strategy (margin paid off, then a low-cost global-equity/bond glide path).
2. **Recommends adjustments.** Each one comes with a measurable trigger for *when* to act: margin
   thresholds, position caps, drift bands, macro signals, and age milestones (CPP/OAS, RRIF).
3. **Scans the economy for investable trends.** It pulls live Bank of Canada and FRED data plus Google News
   flow, scores 12 themes, and tracks rank changes between scans.
4. **Builds an HTML dashboard** (`reports/retirement-dashboard.html`) with an interactive projection chart.

## Quick start

```bash
# 1. Your data (gitignored; this repo is public, so personal files never get committed)
cp data/holdings.example.csv data/holdings.csv     # or import from IBKR, below
cp data/profile.example.json data/profile.json     # set age, savings, spending, CPP estimate

# 2. Run
python3 -m retirement trends        # scan the economy (about 30 s, no API keys needed)
python3 -m retirement recommend     # forecast + recommendations + adjustment calendar
python3 -m retirement report        # build reports/retirement-dashboard.html
python3 -m retirement report --refresh-trends   # scan, then build
```

Without `data/holdings.csv` / `data/profile.json`, the committed example files are used.

### Import from Interactive Brokers

IBKR Portal → Performance & Reports → Statements → **Activity** → format **CSV**, then:

```bash
python3 -m retirement import-ibkr ~/Downloads/U1234567_activity.csv --usdcad 1.41
```

Then check the `asset_class` column in `data/holdings.csv`. Known symbols keep their class; new ones are
guessed. Valid classes are listed in `retirement/assumptions.py`.

## Updating the trend search

| How | What it does |
|---|---|
| `python3 -m retirement trends` | Fetches macro data and news, re-ranks themes, saves `data/trends/<date>.json` and `reports/trends-<date>.md` |
| `/trends [focus]` in Claude Code | Runs the scan and rebuilds the dashboard, then web-researches the top movers and writes `reports/trends-brief-<date>.md` |
| GitHub Action `Monthly trend scan` | Runs on the 1st of each month (or manually from the Actions tab) and commits the new scan |

**Theme score** = macro fit (does the current rate / inflation / growth / credit / oil / FX regime favour the
theme?) + news momentum (±1) + headline tone (±1). Macro fit dominates, so a news frenzy alone cannot make a
theme an "overweight candidate". Edit themes, queries and macro sensitivities in `THEMES` in
`retirement/trends.py`.

## Model notes

- Returns are lognormal annual draws, using a constant-correlation model across asset classes. Assumptions
  in `retirement/assumptions.py` are anchored to FP Canada projection guidelines. Leveraged and
  return-of-capital products carry an extra annual drag for NAV erosion.
- Current strategy: interest on the margin loan is paid from savings and the loan is repaid at retirement.
  Recommended strategy: savings pay down the margin first.
- CPP/OAS use the statutory deferral/early factors. Withdrawals are grossed up by an average tax rate.
- "Success" = the portfolio lasts to `plan_to_age`. Solvers find the savings rate, spending level and
  retirement age that each hit `target_success_probability` on their own.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

_Planning tool, not investment advice._
