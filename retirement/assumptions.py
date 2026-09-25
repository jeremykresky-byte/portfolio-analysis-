"""Capital-market assumptions per asset class.

Expected returns are long-run nominal CAD figures anchored to the FP Canada /
IQPF Projection Assumption Guidelines (equities ~6.6%, fixed income ~3.4%,
cash ~2.4%, inflation ~2.1%). Volatilities and the extra "drag" for
leveraged/closed-end products are this tool's own estimates. Edit freely.

  mu     expected arithmetic annual return, before `drag`
  sigma  annual volatility
  drag   annual cost not captured in price history: fees, leverage cost,
         return-of-capital NAV erosion
  equity share of the class that behaves like equity (for glide-path checks)
"""

ASSET_CLASSES = {
    "cash":            {"label": "Cash",                     "mu": 0.024, "sigma": 0.01, "drag": 0.000, "equity": 0.0},
    "bonds":           {"label": "Bonds",                    "mu": 0.034, "sigma": 0.06, "drag": 0.001, "equity": 0.0},
    "global_equity":   {"label": "Global equity (index)",    "mu": 0.066, "sigma": 0.16, "drag": 0.002, "equity": 1.0},
    "cdn_equity":      {"label": "Canadian equity",          "mu": 0.066, "sigma": 0.17, "drag": 0.000, "equity": 1.0},
    "us_equity":       {"label": "US equity",                "mu": 0.066, "sigma": 0.18, "drag": 0.000, "equity": 1.0},
    "reit":            {"label": "REITs",                    "mu": 0.060, "sigma": 0.20, "drag": 0.000, "equity": 0.8},
    "preferred":       {"label": "Preferred shares",         "mu": 0.045, "sigma": 0.10, "drag": 0.000, "equity": 0.3},
    "mortgage_credit": {"label": "Mortgage / private credit", "mu": 0.055, "sigma": 0.14, "drag": 0.005, "equity": 0.5},
    "bdc":             {"label": "BDCs",                     "mu": 0.065, "sigma": 0.24, "drag": 0.000, "equity": 0.8},
    "split_share":     {"label": "Split-share capital",      "mu": 0.050, "sigma": 0.30, "drag": 0.000, "equity": 1.0},
    "leveraged_cef":   {"label": "Leveraged / ROC CEFs",     "mu": 0.050, "sigma": 0.22, "drag": 0.015, "equity": 1.0},
    "covered_call":    {"label": "Covered-call ETFs",        "mu": 0.050, "sigma": 0.14, "drag": 0.006, "equity": 1.0},
}

# Classes that carry embedded leverage, return-of-capital, or structural
# decay risk. Used by the recommendation engine.
HIGH_RISK_INCOME = {"split_share", "leveraged_cef", "bdc", "covered_call", "mortgage_credit"}

# Pairwise correlation between any two risky classes (constant-correlation model).
RISKY_CORRELATION = 0.6


def net_return(asset_class):
    a = ASSET_CLASSES[asset_class]
    return a["mu"] - a["drag"]
