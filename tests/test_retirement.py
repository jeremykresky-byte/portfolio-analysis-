import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from retirement import forecast as fc
from retirement import recommend, trends
from retirement.ibkr_import import convert
from retirement.portfolio import DATA, load_holdings, load_profile, mix_stats

EXAMPLE_HOLDINGS = DATA / "holdings.example.csv"
EXAMPLE_PROFILE = DATA / "profile.example.json"


def small_profile(**over):
    p = load_profile(EXAMPLE_PROFILE)
    p.update(simulations=400, **over)
    return p


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.pf = load_holdings(EXAMPLE_HOLDINGS)

    def test_debt_and_net_worth(self):
        self.assertAlmostEqual(self.pf.debt, 9000.0)
        self.assertAlmostEqual(self.pf.net_worth, self.pf.gross_assets - 9000.0)
        self.assertAlmostEqual(sum(self.pf.weights_by_class().values()), 1.0)

    def test_mix_stats_diversification(self):
        mu, sigma = mix_stats({"global_equity": 0.5, "bonds": 0.5})
        self.assertLess(sigma, 0.5 * 0.16 + 0.5 * 0.06)
        self.assertGreater(mu, 0.03)


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.pf = load_holdings(EXAMPLE_HOLDINGS)

    def run_sim(self, profile, **kw):
        shocks = fc.Shocks(profile["simulations"], profile["plan_to_age"] - profile["current_age"], profile["seed"])
        return fc.simulate(profile, self.pf, "recommended", shocks, **kw)

    def test_deterministic_with_seed(self):
        p = small_profile()
        self.assertEqual(self.run_sim(p)["success"], self.run_sim(p)["success"])

    def test_more_savings_never_hurts(self):
        p = small_profile()
        low = self.run_sim(p, contribution=5_000)["success"]
        high = self.run_sim(p, contribution=60_000)["success"]
        self.assertGreaterEqual(high, low)

    def test_more_spending_never_helps(self):
        p = small_profile()
        self.assertGreaterEqual(self.run_sim(p, spending=40_000)["success"],
                                self.run_sim(p, spending=120_000)["success"])

    def test_glide_path_bounds(self):
        self.assertEqual(fc.glide_path_equity(30, 62), 0.90)
        self.assertAlmostEqual(fc.glide_path_equity(62, 62), 0.55)
        self.assertEqual(fc.glide_path_equity(90, 62), 0.40)

    def test_cpp_oas_factors(self):
        self.assertAlmostEqual(fc._cpp_factor(70), 1.42)
        self.assertAlmostEqual(fc._cpp_factor(60), 0.64)
        self.assertAlmostEqual(fc._oas_factor(70), 1.36)


class RecommendTests(unittest.TestCase):
    def test_flags_margin_and_concentration(self):
        pf = load_holdings(EXAMPLE_HOLDINGS)
        p = small_profile()
        res = fc.run_forecast(p, pf, with_solvers=False)
        recs = recommend.build(p, pf, res)
        titles = " ".join(r["title"] for r in recs)
        self.assertIn("Margin loan", titles)
        self.assertIn("XEQT", titles)  # >10% single position
        self.assertTrue(all(r["trigger"] for r in recs))

    def test_calendar_is_sorted_and_future(self):
        p = small_profile()
        cal = recommend.adjustment_calendar(p)
        ages = [e["age"] for e in cal]
        self.assertEqual(ages, sorted(ages))
        self.assertTrue(all(a >= p["current_age"] for a in ages))


class TrendTests(unittest.TestCase):
    def test_regime_flags(self):
        macro = {"goc_2y": {"value": 3.4, "chg_3m": 0.64, "chg_12m": 0.9},
                 "boc_policy_rate": {"value": 2.25, "chg_3m": 0, "chg_12m": -0.25},
                 "hy_spread": {"value": 6.0, "chg_3m": 1.5, "chg_12m": 2}}
        f = trends.macro_regime(macro)
        self.assertTrue(f["rates_rising"]["on"])
        self.assertFalse(f["rates_falling"]["on"])
        self.assertTrue(f["credit_stress"]["on"])

    def test_news_momentum(self):
        now = datetime.now(timezone.utc)
        items = [{"title": "Record demand surge", "published": (now - timedelta(days=d)).isoformat()}
                 for d in [0.5] * 10 + [20] * 2]
        m = trends.news_metrics(items)
        self.assertGreater(m["momentum"], 1)
        self.assertGreater(m["tone"], 0)

    def test_macro_fit_dominates_news(self):
        flags = {"rates_rising": {"on": True}}
        hot = {"momentum": 50, "tone": 1}
        score, _ = trends.score_theme(trends.THEMES["bonds_duration"], hot, flags)
        self.assertLess(score, 2.5)  # a news frenzy alone can't make a theme an overweight candidate

    def test_offline_scan_markdown(self):
        snap = {"generated": "2026-01-01T00:00:00", "previous_generated": None, "macro": {}, "regime": {},
                "themes": [], "errors": []}
        self.assertIn("Theme ranking", trends.to_markdown(snap))


class ImportTests(unittest.TestCase):
    def test_ibkr_activity_statement(self):
        csv_text = "\n".join([
            "Statement,Header,Field Name,Field Value",
            "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Description,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code",
            "Open Positions,Data,Summary,Stocks,CAD,XEQT,ISHARES CORE EQUITY ETF PORTFOLIO,100,1,30,3000,35,3500,500,",
            "Open Positions,Data,Summary,Stocks,USD,PSEC,PROSPECT CAPITAL CORP,10,1,5,50,4,40,-10,",
            "Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures",
            "Cash Report,Data,Ending Cash,CAD,-1000,-1000,0",
            "Cash Report,Data,Ending Cash,Base Currency Summary,-1000,-1000,0",
        ])
        with tempfile.TemporaryDirectory() as d:
            stmt = Path(d) / "s.csv"
            stmt.write_text(csv_text)
            out = Path(d) / "holdings.csv"
            rows = convert(stmt, usdcad=1.4, out_path=out)
            self.assertEqual(len(rows), 3)
            psec = next(r for r in rows if r["symbol"] == "PSEC")
            self.assertAlmostEqual(psec["market_value_cad"], 56.0)
            pf = load_holdings(out)
            self.assertAlmostEqual(pf.debt, 1000.0)
            self.assertEqual(next(h for h in pf.holdings if h.symbol == "XEQT").asset_class, "global_equity")


if __name__ == "__main__":
    unittest.main()
