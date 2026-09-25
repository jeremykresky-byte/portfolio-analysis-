"""Render a self-contained HTML dashboard (no external JS; Google Fonts only)."""
import html
import json
from datetime import date
from pathlib import Path

from .assumptions import ASSET_CLASSES, HIGH_RISK_INCOME
from .forecast import glide_path_equity

TEMPLATE = Path(__file__).with_name("dashboard.html")


def _payload(profile, portfolio, res, recs, cal, snap):
    def scen(r):
        return {"success": r["success"], "median_ret": r["median_at_retirement"], "p10_ret": r["p10_at_retirement"],
                "exp_return": r["expected_return"], "vol": r["volatility"], "levers": r.get("levers"),
                "depletion": r["median_depletion_age"],
                "bands": {str(k): [round(x) for x in v] for k, v in r["bands"].items()}}

    alloc = [{"key": c, "label": ASSET_CLASSES[c]["label"], "weight": w, "flag": c in HIGH_RISK_INCOME}
             for c, w in portfolio.weights_by_class().items()]
    positions = [{"symbol": h.symbol, "desc": h.description, "cls": ASSET_CLASSES[h.asset_class]["label"],
                  "value": h.market_value, "pl": h.market_value - h.cost_basis, "ccy": h.currency,
                  "account": h.account}
                 for h in sorted(portfolio.holdings, key=lambda h: -h.market_value)]
    return {
        "generated": date.today().isoformat(),
        "profile": {k: profile[k] for k in ("current_age", "retirement_age", "plan_to_age", "annual_contribution",
                                            "retirement_spending", "inflation", "target_success_probability",
                                            "simulations")},
        "portfolio": {"assets": portfolio.gross_assets, "debt": portfolio.debt, "net": portfolio.net_worth,
                      "leverage": portfolio.leverage, "equity": portfolio.equity_share(),
                      "equity_target": glide_path_equity(profile["current_age"], profile["retirement_age"]),
                      "usd": portfolio.usd_share(), "hri": portfolio.high_risk_income_share(),
                      "alloc": alloc, "positions": positions,
                      "accounts": [{"name": k, "value": v} for k, v in portfolio.by_account().items()]},
        "ages": res["current"]["ages"],
        "current": scen(res["current"]),
        "recommended": scen(res["recommended"]),
        "policy_rate": res["policy_rate"],
        "margin_rate": res["policy_rate"] + profile["margin_rate_spread"],
        "recs": recs,
        "calendar": cal,
        "trends": snap,
    }


def render(profile, portfolio, res, recs, cal, snap, out_path):
    data = _payload(profile, portfolio, res, recs, cal, snap)
    blob = json.dumps(data, default=float).replace("</", "<\\/")
    page = TEMPLATE.read_text().replace("/*__DATA__*/null", blob)
    page = page.replace("__GENERATED__", html.escape(data["generated"]))
    page = page.replace("__DATA_NOTE__", html.escape(profile.get("data_note", "")))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    return out
