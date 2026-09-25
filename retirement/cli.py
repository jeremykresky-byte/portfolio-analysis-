"""Command line entry point: python -m retirement <command>"""
import argparse
import json
import sys

from . import forecast as fc
from . import recommend, trends
from .portfolio import ROOT, add_other_assets, load_holdings, load_profile


def _load(args):
    profile = load_profile(args.profile)
    portfolio = add_other_assets(load_holdings(args.holdings), profile)
    snap = trends.load_latest()
    policy = snap["macro"]["boc_policy_rate"]["value"] / 100 if snap and "boc_policy_rate" in snap.get("macro", {}) \
        else fc.DEFAULT_POLICY_RATE
    return profile, portfolio, snap, policy


def _money(x):
    return f"${x:,.0f}"


def cmd_forecast(args):
    profile, portfolio, snap, policy = _load(args)
    res = fc.run_forecast(profile, portfolio, policy, with_solvers=not args.quick)
    print(f"Net worth today: {_money(portfolio.net_worth)}  (assets {_money(portfolio.gross_assets)}, "
          f"margin {_money(portfolio.debt)})")
    print(f"Age {profile['current_age']} -> retire {profile['retirement_age']}, plan to {profile['plan_to_age']}, "
          f"spending {_money(profile['retirement_spending'])}/yr today's $\n")
    for k in ("current", "recommended"):
        r = res[k]
        print(f"[{k}]  exp. return {r['expected_return']:.1%}  vol {r['volatility']:.1%}")
        print(f"  success probability      {r['success']:.0%}")
        print(f"  median at retirement     {_money(r['median_at_retirement'])} (10th pct {_money(r['p10_at_retirement'])})")
        if r.get("levers"):
            L = r["levers"]
            print(f"  to hit {profile['target_success_probability']:.0%}: save {_money(L['required_contribution'])}/yr, "
                  f"or spend {_money(L['sustainable_spending'])}/yr, or retire at {L['earliest_retirement_age'] or '70+'}")
        print()
    if args.json:
        with open(args.json, "w") as f:
            json.dump(res, f, indent=2)
    return profile, portfolio, snap, res


def cmd_recommend(args):
    profile, portfolio, snap, res = cmd_forecast(args)
    print("=" * 72)
    for r in recommend.build(profile, portfolio, res, snap):
        print(f"[{r['severity'].upper()}] {r['title']}\n  why:  {r['detail']}\n  do:   {r['action']}\n  when: {r['trigger']}\n")
    print("Adjustment calendar")
    for e in recommend.adjustment_calendar(profile):
        print(f"  age {e['age']:>3}  {e['when']:<14} {e['what']}")


def cmd_trends(args):
    snap = trends.run_scan(themes=args.themes or None)
    print(trends.to_markdown(snap))
    print(f"Saved data/trends/{snap['generated'][:10]}.json and reports/trends-{snap['generated'][:10]}.md")


def cmd_report(args):
    from . import report
    if args.refresh_trends:
        trends.run_scan()
    profile, portfolio, snap, policy = _load(args)
    res = fc.run_forecast(profile, portfolio, policy)
    recs = recommend.build(profile, portfolio, res, snap)
    cal = recommend.adjustment_calendar(profile)
    out = report.render(profile, portfolio, res, recs, cal, snap, args.out)
    print(f"Wrote {out}")


def cmd_import(args):
    from .ibkr_import import convert
    rows = convert(args.statement, args.usdcad)
    print(f"Imported {len(rows)} rows into data/holdings.csv. Check the asset_class column.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="retirement", description="Retirement forecast, recommendations and trend scan")
    p.add_argument("--profile", help="default: data/profile.json, else data/profile.example.json")
    p.add_argument("--holdings", help="default: data/holdings.csv, else data/holdings.example.csv")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("forecast", help="Monte Carlo forecast, current vs. recommended strategy")
    f.add_argument("--quick", action="store_true", help="skip the lever solvers")
    f.add_argument("--json", help="write raw results to this file")
    f.set_defaults(fn=cmd_forecast)

    r = sub.add_parser("recommend", help="forecast + recommendations + adjustment calendar")
    r.add_argument("--quick", action="store_true")
    r.add_argument("--json")
    r.set_defaults(fn=cmd_recommend)

    t = sub.add_parser("trends", help="scan the economy and news for investable themes")
    t.add_argument("themes", nargs="*", choices=list(trends.THEMES) + [[]], help="limit to these theme keys")
    t.set_defaults(fn=cmd_trends)

    rp = sub.add_parser("report", help="build the HTML dashboard")
    rp.add_argument("--out", default=str(ROOT / "reports" / "retirement-dashboard.html"))
    rp.add_argument("--refresh-trends", action="store_true", help="run a fresh trend scan first")
    rp.set_defaults(fn=cmd_report)

    im = sub.add_parser("import-ibkr", help="convert an IBKR Activity Statement CSV to holdings.csv")
    im.add_argument("statement")
    im.add_argument("--usdcad", type=float, required=True, help="USD->CAD rate for the statement date")
    im.set_defaults(fn=cmd_import)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
