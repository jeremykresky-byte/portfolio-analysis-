"""Load holdings and compute portfolio-level metrics."""
import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .assumptions import ASSET_CLASSES, HIGH_RISK_INCOME, RISKY_CORRELATION, net_return

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


@dataclass
class Holding:
    symbol: str
    description: str
    asset_class: str
    currency: str
    quantity: float
    market_value: float
    cost_basis: float


@dataclass
class Portfolio:
    holdings: list = field(default_factory=list)

    @property
    def long_positions(self):
        return [h for h in self.holdings if h.market_value > 0]

    @property
    def gross_assets(self):
        return sum(h.market_value for h in self.long_positions)

    @property
    def debt(self):
        return -sum(h.market_value for h in self.holdings if h.market_value < 0)

    @property
    def net_worth(self):
        return self.gross_assets - self.debt

    @property
    def leverage(self):
        """Margin debt as a share of gross assets."""
        return self.debt / self.gross_assets if self.gross_assets else 0.0

    def weights_by_class(self):
        w = {}
        for h in self.long_positions:
            w[h.asset_class] = w.get(h.asset_class, 0.0) + h.market_value
        total = self.gross_assets or 1.0
        return {k: v / total for k, v in sorted(w.items(), key=lambda kv: -kv[1])}

    def position_weights(self):
        total = self.gross_assets or 1.0
        return sorted(((h.symbol, h.market_value / total) for h in self.long_positions),
                      key=lambda kv: -kv[1])

    def equity_share(self):
        return sum(w * ASSET_CLASSES[c]["equity"] for c, w in self.weights_by_class().items())

    def high_risk_income_share(self):
        return sum(w for c, w in self.weights_by_class().items() if c in HIGH_RISK_INCOME)

    def usd_share(self):
        usd = sum(h.market_value for h in self.long_positions if h.currency == "USD")
        return usd / (self.gross_assets or 1.0)

    def unrealized_pl(self):
        return sum(h.market_value - h.cost_basis for h in self.long_positions)


def mix_stats(weights):
    """Expected return and volatility of a class-weight mix (constant correlation)."""
    mu = sum(w * net_return(c) for c, w in weights.items())
    var = 0.0
    items = list(weights.items())
    for i, (ci, wi) in enumerate(items):
        si = ASSET_CLASSES[ci]["sigma"]
        for j, (cj, wj) in enumerate(items):
            sj = ASSET_CLASSES[cj]["sigma"]
            if i == j:
                rho = 1.0
            elif "cash" in (ci, cj):
                rho = 0.0
            elif "bonds" in (ci, cj):
                rho = 0.2
            else:
                rho = RISKY_CORRELATION
            var += wi * wj * si * sj * rho
    return mu, math.sqrt(var)


def _data_file(name, example):
    """Prefer the private file; fall back to the committed example."""
    real = DATA / name
    return real if real.exists() else DATA / example


def load_holdings(path=None):
    path = Path(path or _data_file("holdings.csv", "holdings.example.csv"))
    holdings = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            cls = row["asset_class"].strip()
            if cls not in ASSET_CLASSES:
                raise ValueError(f"{row['symbol']}: unknown asset_class '{cls}'. "
                                 f"Use one of: {', '.join(ASSET_CLASSES)}")
            holdings.append(Holding(
                symbol=row["symbol"].strip(),
                description=row.get("description", "").strip(),
                asset_class=cls,
                currency=row.get("currency", "CAD").strip() or "CAD",
                quantity=float(row.get("quantity") or 0),
                market_value=float(row["market_value_cad"]),
                cost_basis=float(row.get("cost_basis_cad") or row["market_value_cad"]),
            ))
    return Portfolio(holdings)


def load_profile(path=None):
    with open(path or _data_file("profile.json", "profile.example.json")) as f:
        return json.load(f)


def add_other_assets(portfolio, profile):
    """Fold accounts held outside the brokerage file into the portfolio."""
    for a in profile.get("other_assets", []):
        if a.get("value"):
            portfolio.holdings.append(Holding(
                symbol=a["name"][:24], description=a["name"], asset_class=a["asset_class"],
                currency="CAD", quantity=1, market_value=float(a["value"]),
                cost_basis=float(a["value"])))
    return portfolio
