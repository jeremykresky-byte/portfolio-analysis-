"""Monte Carlo retirement forecast.

Simulates nominal portfolio values year by year from the current age to
`plan_to_age`, then reports everything in today's dollars. Two strategies are
compared:

  current      - hold today's asset-class mix, carry the margin loan
  recommended  - direct savings to paying off margin first, then follow an
                 age-based glide path of low-cost global equity + bonds
"""
import math
import random

from .portfolio import mix_stats

DEFAULT_POLICY_RATE = 0.0225  # BoC overnight rate, used when no trends snapshot exists


def glide_path_equity(age, retirement_age):
    """Target equity share by age: 90% until 45, glide to 55% at retirement, 40% by 75."""
    if age <= 45:
        return 0.90
    if age <= retirement_age:
        span = max(retirement_age - 45, 1)
        return 0.90 - (0.90 - 0.55) * (age - 45) / span
    return max(0.40, 0.55 - 0.15 * (age - retirement_age) / max(75 - retirement_age, 1))


def glide_weights(age, retirement_age):
    eq = glide_path_equity(age, retirement_age)
    return {"global_equity": eq, "bonds": 1.0 - eq}


def _lognormal_params(mu, sigma):
    s2 = math.log(1 + sigma ** 2 / (1 + mu) ** 2)
    return math.log(1 + mu) - s2 / 2, math.sqrt(s2)


class Shocks:
    """Common random numbers so scenarios and solver steps are comparable."""

    def __init__(self, n_sims, n_years, seed):
        rng = random.Random(seed)
        self.z = [[rng.gauss(0, 1) for _ in range(n_years)] for _ in range(n_sims)]

    def subset(self, n):
        s = Shocks.__new__(Shocks)
        s.z = self.z[:n]
        return s


def simulate(profile, portfolio, strategy, shocks, *, policy_rate=DEFAULT_POLICY_RATE,
             contribution=None, spending=None, retirement_age=None):
    age0 = profile["current_age"]
    ret_age = retirement_age or profile["retirement_age"]
    end_age = profile["plan_to_age"]
    years = end_age - age0
    infl = profile["inflation"]
    contrib0 = profile["annual_contribution"] if contribution is None else contribution
    spend0 = profile["retirement_spending"] if spending is None else spending
    tax = profile["avg_tax_rate_in_retirement"]
    gb = profile["government_benefits"]
    margin_rate = policy_rate + profile["margin_rate_spread"]

    if strategy == "current":
        start_stats = mix_stats(portfolio.weights_by_class())
        yearly = [_lognormal_params(*start_stats)] * years
    else:
        start_stats = mix_stats(glide_weights(age0, ret_age))
        yearly = [_lognormal_params(*mix_stats(glide_weights(age0 + t, ret_age))) for t in range(years)]

    paths, failures, depletion_ages = [], 0, []
    for z in shocks.z:
        assets, debt = portfolio.gross_assets, portfolio.debt
        path = [assets - debt]
        failed = False
        for t in range(years):
            age = age0 + t
            deflator = (1 + infl) ** t
            m, s = yearly[t]
            assets *= math.exp(m + s * z[t])
            interest = debt * margin_rate
            if age < ret_age:
                c = contrib0 * (1 + profile["contribution_growth"]) ** t
                if strategy == "current":
                    # Interest is paid from savings; the loan balance is carried.
                    assets += c - interest
                else:
                    debt += interest
                    paydown = min(debt, c)
                    debt -= paydown
                    assets += c - paydown
            else:
                if debt > 0:  # clear any remaining margin at retirement
                    assets -= debt + interest
                    debt = 0.0
                benefits = 0.0
                if age >= gb["cpp_start_age"]:
                    benefits += gb["cpp_monthly_at_65"] * 12 * _cpp_factor(gb["cpp_start_age"])
                if age >= gb["oas_start_age"]:
                    benefits += gb["oas_monthly_at_65"] * 12 * _oas_factor(gb["oas_start_age"])
                need = max(spend0 - benefits, 0.0) / (1 - tax)
                assets -= need * deflator
            if assets - debt <= 0 and not failed:
                failed = True
                depletion_ages.append(age + 1)
                assets, debt = 0.0, 0.0
            path.append((assets - debt) / ((1 + infl) ** (t + 1)))
        failures += failed
        paths.append(path)

    n = len(shocks.z)
    ages = list(range(age0, end_age + 1))
    bands = {q: [] for q in (10, 25, 50, 75, 90)}
    for i in range(len(ages)):
        col = sorted(p[i] for p in paths)
        for q in bands:
            bands[q].append(col[min(int(q / 100 * n), n - 1)])
    ret_idx = ret_age - age0
    at_ret = sorted(p[ret_idx] for p in paths)
    depletion_ages.sort()
    return {
        "strategy": strategy,
        "ages": ages,
        "bands": bands,
        "success": 1 - failures / n,
        "median_at_retirement": at_ret[n // 2],
        "p10_at_retirement": at_ret[n // 10],
        "median_depletion_age": depletion_ages[len(depletion_ages) // 2] if depletion_ages else None,
        "expected_return": start_stats[0],
        "volatility": start_stats[1],
    }


def _cpp_factor(start_age):
    """CPP: -0.6%/month before 65, +0.7%/month after (max 70)."""
    months = (start_age - 65) * 12
    return 1 + (0.007 * months if months > 0 else 0.006 * months)


def _oas_factor(start_age):
    """OAS: +0.6%/month deferred past 65, max 70."""
    return 1 + 0.006 * max(0, (min(start_age, 70) - 65) * 12)


def _bisect(fn, lo, hi, target, increasing=True, iters=18):
    for _ in range(iters):
        mid = (lo + hi) / 2
        ok = fn(mid) >= target
        if ok == increasing:
            hi = mid
        else:
            lo = mid
    return hi if increasing else lo


def solve(profile, portfolio, strategy, shocks, policy_rate):
    """Levers that hit the target success probability, holding everything else fixed."""
    target = profile["target_success_probability"]
    small = shocks.subset(1500)

    def success(**kw):
        return simulate(profile, portfolio, strategy, small, policy_rate=policy_rate, **kw)["success"]

    contrib = _bisect(lambda c: success(contribution=c), 0, 250_000, target, increasing=True)
    spend = _bisect(lambda s: success(spending=s), 0, 400_000, target, increasing=False)
    earliest = None
    for age in range(profile["current_age"] + 1, 71):
        if success(retirement_age=age) >= target:
            earliest = age
            break
    return {"required_contribution": round(contrib, -2),
            "sustainable_spending": round(spend, -2),
            "earliest_retirement_age": earliest}


def run_forecast(profile, portfolio, policy_rate=DEFAULT_POLICY_RATE, with_solvers=True):
    years = profile["plan_to_age"] - profile["current_age"]
    shocks = Shocks(profile["simulations"], years, profile["seed"])
    out = {}
    for strategy in ("current", "recommended"):
        res = simulate(profile, portfolio, strategy, shocks, policy_rate=policy_rate)
        if with_solvers:
            res["levers"] = solve(profile, portfolio, strategy, shocks, policy_rate)
        out[strategy] = res
    out["policy_rate"] = policy_rate
    return out
