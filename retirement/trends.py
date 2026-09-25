"""Economic trend scanner.

Pulls live macro data (Bank of Canada Valet API, FRED) and recent news flow
(Google News RSS) for a watchlist of investable themes, then ranks the themes
by a combined score:

  news momentum  - article count in the last 7 days vs. the prior 3 weeks
  news tone      - simple positive/negative keyword balance in headlines
  macro fit      - does today's rate / inflation / growth regime favour the theme?

Every run is saved to data/trends/<date>.json and data/trends/latest.json, and a
markdown brief is written to reports/, so rank changes can be tracked over time.
No API keys are needed.
"""
import csv
import io
import json
import math
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRENDS_DIR = ROOT / "data" / "trends"
REPORTS_DIR = ROOT / "reports"
# FRED silently stalls requests from unrecognised browser-like user agents;
# Python's default urllib agent is accepted by all three sources.
UA = {}

# Each theme: news query, example Canadian/US-listed vehicles, and which macro
# conditions help (+) or hurt (-) it. Tickers are examples to research, not advice.
THEMES = {
    "ai_infrastructure": {
        "label": "AI infrastructure & data centres",
        "query": '"data center" OR "AI infrastructure" capex',
        "vehicles": ["XIT.TO", "SMH", "SRVR"],
        "macro": {"rates_rising": -1, "rates_falling": 1, "risk_on": 1, "recession_risk": -1},
    },
    "power_grid": {
        "label": "Power generation & grid build-out",
        "query": '"power grid" OR "electricity demand" OR "utility capex"',
        "vehicles": ["ZUT.TO", "BEP.UN.TO", "GRID"],
        "macro": {"rates_rising": -1, "rates_falling": 1, "inflation_high": 0, "recession_risk": 0},
    },
    "nuclear_uranium": {
        "label": "Nuclear & uranium",
        "query": "uranium OR \"small modular reactor\" OR \"nuclear power\"",
        "vehicles": ["CCO.TO", "HURA.TO", "URNM"],
        "macro": {"inflation_high": 1, "risk_on": 1},
    },
    "agriculture_food": {
        "label": "Agriculture & food security",
        "query": "farmland OR fertilizer OR \"food security\" OR \"crop prices\"",
        "vehicles": ["NTR.TO", "COW.TO", "MOO"],
        "macro": {"inflation_high": 1, "oil_high": 1, "usd_strong": -1},
    },
    "energy_lng": {
        "label": "Canadian energy & LNG",
        "query": "\"LNG Canada\" OR \"oil sands\" OR \"natural gas prices\"",
        "vehicles": ["XEG.TO", "TOU.TO", "ENB.TO"],
        "macro": {"oil_high": 2, "inflation_high": 1, "recession_risk": -1},
    },
    "infrastructure_defence": {
        "label": "Infrastructure & defence spending",
        "query": "\"defence spending\" OR \"infrastructure spending\" Canada",
        "vehicles": ["ZGI.TO", "CAE.TO", "ITA"],
        "macro": {"inflation_high": 0, "recession_risk": 0, "rates_falling": 1},
    },
    "healthcare_aging": {
        "label": "Healthcare & aging population",
        "query": "\"aging population\" OR \"healthcare spending\" OR \"GLP-1\"",
        "vehicles": ["XHC.TO", "XLV"],
        "macro": {"recession_risk": 1, "risk_on": -1},
    },
    "gold_hard_assets": {
        "label": "Gold & precious metals",
        "query": "\"gold price\" OR \"central bank gold\"",
        "vehicles": ["XGD.TO", "CGL.TO", "GLD"],
        "macro": {"rates_rising": -1, "rates_falling": 1, "inflation_high": 1, "recession_risk": 1, "usd_strong": -1},
    },
    "banks_financials": {
        "label": "Canadian banks & financials",
        "query": "\"Canadian banks\" earnings OR \"bank stocks\"",
        "vehicles": ["ZEB.TO", "XFN.TO"],
        "macro": {"rates_rising": 1, "curve_steepening": 2, "recession_risk": -2, "credit_stress": -2},
    },
    "reits_real_estate": {
        "label": "REITs & rate-sensitive real estate",
        "query": "REIT OR \"commercial real estate\" Canada",
        "vehicles": ["XRE.TO", "ZRE.TO"],
        "macro": {"rates_rising": -2, "rates_falling": 2, "credit_stress": -2, "recession_risk": -1},
    },
    "cybersecurity": {
        "label": "Cybersecurity",
        "query": "cybersecurity spending OR \"cyber attack\" enterprise",
        "vehicles": ["CIBR", "HACK"],
        "macro": {"risk_on": 1, "recession_risk": 0},
    },
    "bonds_duration": {
        "label": "Government bonds (duration)",
        "query": "\"bond yields\" OR \"Bank of Canada\" rate",
        "vehicles": ["XBB.TO", "ZFL.TO"],
        "macro": {"rates_rising": -2, "rates_falling": 2, "recession_risk": 2, "inflation_high": -2},
    },
}

POSITIVE = {"surge", "soar", "record", "boom", "growth", "demand", "rally", "beat", "strong",
            "expand", "expansion", "invest", "investment", "gain", "gains", "rise", "rises",
            "jump", "upgrade", "shortage", "wins", "approve", "approved", "accelerate"}
NEGATIVE = {"slump", "plunge", "fall", "falls", "drop", "cut", "cuts", "weak", "loss", "losses",
            "bankrupt", "default", "glut", "downgrade", "layoffs", "crash", "fear", "fears",
            "tariff", "tariffs", "slowdown", "warn", "warning", "probe", "delay"}


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def boc_series(series, recent=260):
    data = json.loads(_get(f"https://www.bankofcanada.ca/valet/observations/{series}/json?recent={recent}"))
    obs = [(o["d"], float(o[series]["v"])) for o in data["observations"] if o.get(series, {}).get("v")]
    return sorted(obs)  # Valet returns `recent=` results newest first


def fred_series(series, days=400):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = list(csv.reader(io.StringIO(
        _get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={cutoff}"))))
    out = []
    for d, v in rows[1:]:
        if d >= cutoff and v not in (".", ""):
            out.append((d, float(v)))
    return out


def _value_ago(series, days):
    if not series:
        return None
    target = (datetime.strptime(series[-1][0], "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    older = [v for d, v in series if d <= target]
    return older[-1] if older else series[0][1]


MACRO_SOURCES = [
    # key, label, source, series id, unit
    ("boc_policy_rate", "BoC overnight rate", "boc", "V39079", "%"),
    ("goc_2y", "GoC 2-yr yield", "boc", "BD.CDN.2YR.DQ.YLD", "%"),
    ("goc_10y", "GoC 10-yr yield", "boc", "BD.CDN.10YR.DQ.YLD", "%"),
    ("cad_cpi_yoy", "Canada CPI y/y", "boc", "ATOM_V41693242", "%"),
    ("cad_cpi_trim", "Canada CPI-trim", "boc", "CPI_TRIM", "%"),
    ("usdcad", "USD/CAD", "boc", "FXUSDCAD", ""),
    ("us_10y", "US 10-yr Treasury", "fred", "DGS10", "%"),
    ("us_curve", "US 10y-2y spread", "fred", "T10Y2Y", "%"),
    ("us_unemployment", "US unemployment", "fred", "UNRATE", "%"),
    ("sahm", "Sahm recession indicator", "fred", "SAHMREALTIME", "pts"),
    ("hy_spread", "US high-yield spread", "fred", "BAMLH0A0HYM2", "%"),
    ("vix", "VIX", "fred", "VIXCLS", ""),
    ("wti", "WTI crude", "fred", "DCOILWTICO", "US$"),
]


def fetch_macro():
    macro, errors = {}, []
    for key, label, src, sid, unit in MACRO_SOURCES:
        try:
            s = boc_series(sid) if src == "boc" else fred_series(sid)
            macro[key] = {"label": label, "unit": unit, "date": s[-1][0], "value": s[-1][1],
                          "chg_3m": round(s[-1][1] - _value_ago(s, 91), 3),
                          "chg_12m": round(s[-1][1] - _value_ago(s, 365), 3),
                          "source": f"{'Bank of Canada' if src == 'boc' else 'FRED'} {sid}"}
        except Exception as e:  # keep going; one dead series shouldn't kill the scan
            errors.append(f"{key}: {e}")
    return macro, errors


def macro_regime(m):
    """Translate raw macro readings into boolean regime flags with reasons."""
    def v(k, f="value"):
        return m.get(k, {}).get(f)

    flags = {}

    def flag(name, cond, why):
        if cond is not None:
            flags[name] = {"on": bool(cond), "why": why}

    pr12, y2 = v("boc_policy_rate", "chg_12m"), v("goc_2y", "chg_3m")
    # Market yields lead the policy rate, so the 2-yr GoC decides ties.
    flag("rates_falling", pr12 is not None and y2 is not None and (y2 < -0.25 or (pr12 < 0 and y2 <= 0)),
         f"BoC rate {pr12:+.2f} pts over 12 mo; 2-yr GoC {y2:+.2f} over 3 mo" if y2 is not None else "n/a")
    flag("rates_rising", y2 is not None and y2 > 0.25,
         f"2-yr GoC {y2:+.2f} pts over 3 mo (>+0.25 = rising); 10-yr {v('goc_10y', 'chg_3m') or 0:+.2f}"
         if y2 is not None else "n/a")
    cpi = v("cad_cpi_yoy")
    flag("inflation_high", cpi is not None and cpi > 3.0, f"Canada CPI {cpi}% y/y (>3% = high)" if cpi is not None else "n/a")
    sahm, curve = v("sahm"), v("us_curve")
    flag("recession_risk", (sahm or 0) >= 0.3 or (curve is not None and curve < 0),
         f"Sahm {sahm} (>=0.3 warns), US curve {curve}% (<0 = inverted)")
    flag("curve_steepening", (v("us_curve", "chg_12m") or 0) > 0.25 and (curve or 0) > 0,
         f"US 10y-2y {curve}% ({v('us_curve', 'chg_12m') or 0:+.2f} over 12 mo)")
    hy = v("hy_spread")
    flag("credit_stress", hy is not None and (hy > 5.0 or (v("hy_spread", "chg_3m") or 0) > 1.0),
         f"HY spread {hy}% (>5% or +1 pt in 3 mo = stress)")
    vix = v("vix")
    flag("risk_on", vix is not None and vix < 18 and (hy or 9) < 4.0, f"VIX {vix}, HY spread {hy}%")
    oil = v("wti")
    flag("oil_high", oil is not None and oil > 85, f"WTI US${oil} (>85 = high)")
    fx = v("usdcad")
    flag("usd_strong", fx is not None and fx > 1.38, f"USD/CAD {fx} (>1.38 = strong USD)")
    return flags


def fetch_news(query, days=30):
    q = urllib.parse.quote(f"{query} when:{days}d")
    xml = _get(f"https://news.google.com/rss/search?q={q}&hl=en-CA&gl=CA&ceid=CA:en")
    root = ET.fromstring(xml)
    items = []
    for it in root.iter("item"):
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:
            continue
        items.append({"title": it.findtext("title") or "", "link": it.findtext("link") or "",
                      "source": (it.find("source").text if it.find("source") is not None else ""),
                      "published": pub.isoformat()})
    items.sort(key=lambda x: x["published"], reverse=True)
    return items


def news_metrics(items):
    """Momentum = daily article rate in the recent window vs. the rest of the feed.

    Google News returns at most 100 items, so a busy topic may only reach back a
    few days. The comparison window adapts to the span the feed actually covers.
    """
    now = datetime.now(timezone.utc)
    ages = [(now - datetime.fromisoformat(i["published"])).total_seconds() / 86400 for i in items]
    span = max(ages) if ages else 30.0
    window = min(7.0, max(span / 2, 0.5))
    recent = sum(1 for a in ages if a <= window)
    prior = len(ages) - recent
    rate_recent = recent / window
    rate_prior = prior / max(span - window, 0.5)
    momentum = (rate_recent + 0.1) / (rate_prior + 0.1)
    pos = neg = 0
    for i in items:
        words = set(re.findall(r"[a-z\-]+", i["title"].lower()))
        pos += len(words & POSITIVE)
        neg += len(words & NEGATIVE)
    tone = (pos - neg) / max(pos + neg, 1)
    return {"articles": len(items), "span_days": round(span, 1),
            "per_day": round(len(items) / max(span, 0.5), 1),
            "momentum": round(momentum, 2), "tone": round(tone, 2)}


def score_theme(theme, news, flags):
    fit, reasons = 0, []
    for cond, w in theme["macro"].items():
        f = flags.get(cond)
        if f and f["on"] and w:
            fit += w
            reasons.append(f"{'+' if w > 0 else ''}{w} {cond.replace('_', ' ')}")
    mom = news["momentum"] if news else 1.0
    tone = news["tone"] if news else 0.0
    # Macro fit carries the most weight (about -4..+4). News momentum (log2, capped
    # at ±1) and headline tone (-1..+1) nudge the score; they can't carry it alone.
    score = round(max(-1.0, min(1.0, math.log2(max(mom, 0.01)))) + tone + fit, 2)
    return score, reasons


def run_scan(themes=None, pause=1.0, fetch=True):
    """Full scan. Returns the snapshot dict and writes it to disk."""
    themes = themes or list(THEMES)
    macro, errors = fetch_macro() if fetch else ({}, [])
    flags = macro_regime(macro)
    rows = []
    for key in themes:
        t = THEMES[key]
        news, headlines = None, []
        if fetch:
            try:
                items = fetch_news(t["query"])
                news = news_metrics(items)
                headlines = items[:5]
            except Exception as e:
                errors.append(f"news {key}: {e}")
            time.sleep(pause)
        score, reasons = score_theme(t, news, flags)
        rows.append({"key": key, "label": t["label"], "vehicles": t["vehicles"], "score": score,
                     "news": news, "macro_reasons": reasons, "headlines": headlines})
    rows.sort(key=lambda r: -r["score"])

    previous = load_latest()
    prev_rank = {r["key"]: i + 1 for i, r in enumerate(previous["themes"])} if previous else {}
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["prev_rank"] = prev_rank.get(r["key"])
        r["signal"] = ("Overweight candidate" if r["score"] >= 2.5 else "Watch" if r["score"] >= 0.5
                       else "Avoid / underweight")

    snap = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "previous_generated": previous["generated"] if previous else None,
            "macro": macro, "regime": flags, "themes": rows, "errors": errors}
    save(snap)
    return snap


def load_latest():
    p = TRENDS_DIR / "latest.json"
    return json.loads(p.read_text()) if p.exists() else None


def save(snap):
    TRENDS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    day = snap["generated"][:10]
    body = json.dumps(snap, indent=2)
    (TRENDS_DIR / f"{day}.json").write_text(body)
    (TRENDS_DIR / "latest.json").write_text(body)
    (REPORTS_DIR / f"trends-{day}.md").write_text(to_markdown(snap))


def to_markdown(snap):
    lines = [f"# Economic trend scan, {snap['generated'][:10]}", ""]
    if snap.get("previous_generated"):
        lines.append(f"Compared with the previous scan on {snap['previous_generated'][:10]}.\n")
    lines += ["## Macro regime", "", "| Condition | Status | Reading |", "|---|---|---|"]
    for k, f in snap["regime"].items():
        lines.append(f"| {k.replace('_', ' ')} | {'YES' if f['on'] else 'no'} | {f['why']} |")
    lines += ["", "## Macro readings", "", "| Indicator | Latest | 3-mo chg | 12-mo chg | Date | Source |",
              "|---|---|---|---|---|---|"]
    for m in snap["macro"].values():
        lines.append(f"| {m['label']} | {m['value']}{m['unit'] if m['unit'] in ('%',) else ''} | {m['chg_3m']:+} | "
                     f"{m['chg_12m']:+} | {m['date']} | {m['source']} |")
    lines += ["", "## Theme ranking", "",
              "| Rank | Δ | Theme | Score | Signal | Articles/day | Momentum | Tone | Macro fit | Vehicles to research |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in snap["themes"]:
        d = "new" if r["prev_rank"] is None else f"{r['prev_rank'] - r['rank']:+d}"
        n = r["news"] or {}
        lines.append(f"| {r['rank']} | {d} | {r['label']} | {r['score']} | {r['signal']} | "
                     f"{n.get('per_day', '-')} | {n.get('momentum', '-')} | "
                     f"{n.get('tone', '-')} | {', '.join(r['macro_reasons']) or '-'} | {', '.join(r['vehicles'])} |")
    lines += ["", "## Top headlines", ""]
    for r in snap["themes"][:5]:
        lines.append(f"### {r['label']}")
        for h in r["headlines"][:3]:
            lines.append(f"- [{h['title']}]({h['link']}) ({h['published'][:10]})")
        lines.append("")
    if snap["errors"]:
        lines += ["## Data errors", ""] + [f"- {e}" for e in snap["errors"]]
    lines += ["", "_Scores are a screening signal, not investment advice. Research any vehicle before buying._"]
    return "\n".join(lines) + "\n"
