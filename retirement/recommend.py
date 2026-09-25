"""Rules engine: what to change, and the trigger that says *when* to change it.

Each recommendation carries:
  severity  critical | warning | info | good
  action    the concrete change
  trigger   the measurable condition or date that should prompt the change
"""
from datetime import date

from .assumptions import ASSET_CLASSES
from .forecast import glide_path_equity

SINGLE_POSITION_CAP = 0.10
LEVERAGE_WARN = 0.15
LEVERAGE_CRITICAL = 0.30
HIGH_RISK_INCOME_CAP = 0.20
DRIFT_BAND = 0.05


def _rec(severity, title, detail, action, trigger, category):
    return {"severity": severity, "title": title, "detail": detail, "action": action,
            "trigger": trigger, "category": category}


def build(profile, portfolio, forecast, trends=None):
    recs = []
    age = profile["current_age"]
    ret_age = profile["retirement_age"]
    target = profile["target_success_probability"]
    cur, rec = forecast["current"], forecast["recommended"]
    margin_rate = forecast["policy_rate"] + profile["margin_rate_spread"]

    # 1. Plan health
    s = cur["success"]
    levers = cur.get("levers", {})
    if s >= target:
        recs.append(_rec("good", f"Plan on track: {s:.0%} success", "Current strategy meets the target probability.",
                         "Keep contributions on autopilot.", f"Re-run if success drops below {target:.0%}.", "plan"))
    else:
        sev = "critical" if s < 0.70 else "warning"
        if levers:
            gap = levers["required_contribution"] - profile["annual_contribution"]
            action = (f"Save about ${levers['required_contribution']:,.0f}/yr (+${max(gap, 0):,.0f}), "
                      f"or cut retirement spending to ${levers['sustainable_spending']:,.0f}/yr, "
                      f"or retire at {levers['earliest_retirement_age'] or '70+'}.")
        else:
            action = "Raise savings, lower planned spending, or retire later. Run without --quick to size each lever."
        recs.append(_rec(sev, f"Plan success {s:.0%} vs. {target:.0%} target",
                         f"Holding today's mix, the money runs out in {1 - s:.0%} of simulations "
                         f"(median depletion age {cur.get('median_depletion_age') or 'n/a'}).",
                         action,
                         "Act now. Re-test each January and after any income change of more than 10%.", "plan"))
    rl = rec.get("levers")
    if levers and rl and rl["required_contribution"] < 0.9 * levers["required_contribution"]:
        recs.append(_rec("warning",
                         f"Switching strategy cuts required savings from ${levers['required_contribution']:,.0f} "
                         f"to ${rl['required_contribution']:,.0f}/yr",
                         f"Paying off margin and moving to a low-cost global-equity/bond glide path raises the median "
                         f"at retirement from ${cur['median_at_retirement']:,.0f} to ${rec['median_at_retirement']:,.0f} "
                         f"(today's dollars). Sustainable spending rises from ${levers['sustainable_spending']:,.0f} "
                         f"to ${rl['sustainable_spending']:,.0f}/yr.",
                         "Adopt the recommended strategy in stages: margin first, then trim positions above 10%, "
                         "then move structured income products into index ETFs.",
                         "Start with the next contribution.", "plan"))
    elif rec["success"] - s >= 0.05:
        recs.append(_rec("warning", f"Switching strategy lifts success to {rec['success']:.0%}",
                         f"Paying off margin and moving to a low-cost global-equity/bond glide path raises the median "
                         f"at retirement from ${cur['median_at_retirement']:,.0f} to ${rec['median_at_retirement']:,.0f} "
                         f"(today's dollars).",
                         "Adopt the recommended strategy in stages (steps below).",
                         "Start with the next contribution.", "plan"))

    # 2. Leverage
    lev = portfolio.leverage
    if lev > 0:
        sev = "critical" if lev >= LEVERAGE_CRITICAL else "warning" if lev >= LEVERAGE_WARN else "info"
        recs.append(_rec(sev, f"Margin loan ${portfolio.debt:,.0f} ({lev:.0%} of assets)",
                         f"The loan costs about {margin_rate:.1%}/yr. The income holdings behind it are volatile, so a "
                         f"20-30% drawdown could force a margin call and lock in losses.",
                         "Send every contribution to the loan until it is paid off. Sell the weakest income holdings "
                         "(largest ROC or NAV-erosion names first) to speed this up.",
                         f"Immediately. Stop borrowing whenever margin exceeds {LEVERAGE_WARN:.0%} of assets or the "
                         f"margin rate exceeds the portfolio's expected return ({cur['expected_return']:.1%}).",
                         "risk"))

    # 3. Concentration
    for sym, w in portfolio.position_weights():
        if w > SINGLE_POSITION_CAP:
            recs.append(_rec("warning", f"{sym} is {w:.0%} of the portfolio",
                             f"A single position above {SINGLE_POSITION_CAP:.0%} can derail the plan on its own.",
                             f"Trim {sym} to {SINGLE_POSITION_CAP:.0%} or less and redirect the proceeds.",
                             f"Now, then whenever any position drifts above {SINGLE_POSITION_CAP:.0%}.", "risk"))

    # 4. Yield-chasing / structural decay
    hri = portfolio.high_risk_income_share()
    if hri > HIGH_RISK_INCOME_CAP:
        classes = [ASSET_CLASSES[c]["label"] for c, w in portfolio.weights_by_class().items()
                   if c in ("split_share", "leveraged_cef", "bdc", "covered_call", "mortgage_credit") and w > 0.02]
        recs.append(_rec("critical" if hri > 0.5 else "warning",
                         f"{hri:.0%} in high-yield structured products",
                         f"Holdings in {', '.join(classes)} pay high distributions, but those payouts often come from "
                         f"return of capital or leverage. Over time the NAV erodes (the Cornerstone funds CLM/CRF are the "
                         f"textbook case). This tool charges these classes a {ASSET_CLASSES['leveraged_cef']['drag']:.1%}/yr drag.",
                         f"Cap high-yield structured products at {HIGH_RISK_INCOME_CAP:.0%}. Replace them with broad index "
                         f"ETFs (e.g. XEQT/VEQT for equity, XBB/ZAG for bonds).",
                         "At each quarterly review, sell any fund whose NAV fell more than 5% over 12 months while "
                         "paying its distribution.", "allocation"))

    # 5. Glide path / drift
    eq_now = portfolio.equity_share()
    eq_target = glide_path_equity(age, ret_age)
    if abs(eq_now - eq_target) > DRIFT_BAND:
        recs.append(_rec("warning", f"Equity exposure {eq_now:.0%} vs. {eq_target:.0%} target",
                         f"The glide path for age {age} retiring at {ret_age} calls for {eq_target:.0%} equity-like assets.",
                         "Rebalance with new contributions first, and sell only to close any remaining gap.",
                         f"Rebalance whenever an asset class drifts more than {DRIFT_BAND:.0%} from target, checked quarterly.",
                         "allocation"))

    # 6. Currency
    usd = portfolio.usd_share()
    if usd > 0.30:
        recs.append(_rec("info", f"{usd:.0%} of assets in USD",
                         "Retirement spending will be in CAD. Beyond 30-40% USD, exchange-rate swings become a real source of plan risk.",
                         "Hold US-listed dividend payers inside the RRSP (no 15% US withholding tax). Use CAD-listed "
                         "ETFs in the TFSA.", "When USD/CAD is above 1.38, convert USD holdings to CAD (Norbert's Gambit at IBKR).",
                         "tax"))

    # 7. Account location (always relevant for a Canadian business owner)
    recs.append(_rec("info", "Put each holding in the right account",
                     "REIT, interest and foreign income are taxed as ordinary income. Canadian eligible dividends and "
                     "capital gains get favourable rates.",
                     "Fill the TFSA and RRSP first. Hold REITs, bonds and US stocks in registered accounts and Canadian "
                     "equity in non-registered. If the agency is incorporated, compare an RRSP against retained "
                     "corporate investing and an Individual Pension Plan (IPP) with an accountant.",
                     "Every January (new TFSA room) and before the RRSP deadline, 60 days after year-end.", "tax"))

    # 8. Macro-driven triggers from the latest trend scan
    if trends:
        r = trends.get("regime", {})
        if r.get("credit_stress", {}).get("on"):
            recs.append(_rec("critical", "Credit spreads are widening",
                             r["credit_stress"]["why"], "Cut BDC, split-share and leveraged-CEF exposure first. These fall hardest.",
                             "Now. Stand down when the HY spread falls back below 4.5%.", "macro"))
        if r.get("recession_risk", {}).get("on"):
            recs.append(_rec("warning", "Recession signals are active", r["recession_risk"]["why"],
                             "Shift to the defensive end of your glide-path band (-5% equity). Keep 6-12 months of "
                             "business expenses in cash outside the portfolio.",
                             "Re-check at each monthly trend scan.", "macro"))
        if r.get("rates_rising", {}).get("on"):
            rate_sensitive = sum(w for c, w in portfolio.weights_by_class().items()
                                 if c in ("reit", "preferred", "split_share", "mortgage_credit", "bonds"))
            recs.append(_rec("warning", "Market yields are rising", r["rates_rising"]["why"],
                             f"{rate_sensitive:.0%} of the portfolio is rate-sensitive (REITs, preferreds, split-shares, "
                             f"mortgage credit). Don't add to these. Paying down margin now also protects against "
                             f"higher borrowing costs.",
                             "Re-assess when the 2-yr GoC yield stops rising (3-month change below +0.25 pts).",
                             "macro"))
        if r.get("rates_falling", {}).get("on") and portfolio.debt > 0:
            recs.append(_rec("info", "Rates are falling, so margin cost is easing",
                             r["rates_falling"]["why"],
                             "Cheaper borrowing is not a reason to keep the loan. Keep paying it down.",
                             "No change to the plan.", "macro"))
        top = [t for t in trends.get("themes", []) if t["score"] >= 2.5][:3]
        if top:
            recs.append(_rec("info", "Themes with the strongest current signals",
                             "; ".join(f"{t['label']} (score {t['score']})" for t in top),
                             "Limit thematic bets to a 5-10% satellite sleeve. Keep the core in broad index ETFs.",
                             "Review monthly with `python -m retirement trends`. Exit a theme after two consecutive "
                             "negative scores.", "macro"))

    order = {"critical": 0, "warning": 1, "info": 2, "good": 3}
    recs.sort(key=lambda x: order[x["severity"]])
    return recs


def adjustment_calendar(profile):
    """Dated checkpoints: when to revisit and what to decide."""
    age = profile["current_age"]
    ret = profile["retirement_age"]
    year = date.today().year
    events = [
        (age, "Monthly", "Run the trend scan. Act only if a macro trigger fires."),
        (age, "Quarterly", "Check drift (±5%), position caps (10%) and margin balance."),
        (age, "Every January", "Use new TFSA room, re-run the forecast, and update profile.json with actual savings."),
    ]
    for milestone, note in [
        (45, "Glide path starts: equity target begins falling from 90%."),
        (50, "Mid-career checkpoint: if success is below 85%, raise savings or push back retirement. Review insurance."),
        (55, "Build a 2-year cash and short-bond bucket over the next 5 years to protect against sequence risk."),
        (ret - 1, "Pre-retirement: set the withdrawal order (non-reg → RRSP meltdown → TFSA last). Wind down the business or plan a succession."),
        (ret, "Retirement: switch contributions to withdrawals. Rebalance annually."),
        (65, "OAS/CPP decision: each year of CPP deferral adds 8.4% (to 70) and OAS 7.2%. Defer if the portfolio can bridge."),
        (71, "Convert the RRSP to a RRIF by Dec 31. Minimum withdrawals start the next year."),
    ]:
        if milestone >= age:
            events.append((milestone, str(year + milestone - age), note))
    events.sort(key=lambda e: e[0])
    return [{"age": a, "when": w, "what": n} for a, w, n in events]
