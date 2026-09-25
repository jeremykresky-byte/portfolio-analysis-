"""Convert an IBKR Activity Statement CSV into data/holdings.csv.

In IBKR Portal: Performance & Reports -> Statements -> Activity -> Format: CSV.
The "Open Positions" and "Cash Report" sections are used. Asset classes are
kept from the existing holdings.csv where the symbol is already known, and
guessed otherwise. Review the `asset_class` column after importing.
"""
import csv
from pathlib import Path

from .portfolio import DATA

FIELDS = ["symbol", "description", "asset_class", "currency", "quantity", "market_value_cad", "cost_basis_cad"]


def _guess_class(symbol, description, category, currency):
    d = (description or "").upper()
    if "REIT" in d or "PROPERT" in d:
        return "reit"
    if " PR." in f" {symbol}" or ".PR." in symbol or "PFD" in d or "PREF" in d:
        return "preferred"
    if "SPLIT" in d:
        return "split_share"
    if "COVERED CALL" in d or "COV CALL" in d:
        return "covered_call"
    if "BOND" in d or "AGGREGATE" in d:
        return "bonds"
    if category and "ETF" in category.upper() or any(k in d for k in ("ETF", "ISHARES", "VANGUARD", "INDEX")):
        return "global_equity"
    return "cdn_equity" if currency == "CAD" else "us_equity"


def convert(statement_path, usdcad, out_path=None):
    out_path = Path(out_path or DATA / "holdings.csv")
    known = {}
    if out_path.exists():
        with open(out_path, newline="") as f:
            known = {r["symbol"]: r["asset_class"] for r in csv.DictReader(f)}

    rows, header, cash_header = [], None, None
    fx = {"CAD": 1.0, "USD": usdcad}
    with open(statement_path, newline="", encoding="utf-8-sig") as f:
        for rec in csv.reader(f):
            if len(rec) < 3:
                continue
            section, kind = rec[0], rec[1]
            if section == "Open Positions" and kind == "Header":
                header = rec
            elif section == "Open Positions" and kind == "Data" and header and rec[2] == "Summary":
                r = dict(zip(header, rec))
                cur = r.get("Currency", "CAD")
                rate = fx.get(cur)
                if rate is None:
                    raise ValueError(f"No FX rate for {cur}; only CAD and USD are supported")
                sym = r["Symbol"]
                rows.append({
                    "symbol": sym,
                    "description": r.get("Description", ""),
                    "asset_class": known.get(sym) or _guess_class(sym, r.get("Description", ""), r.get("Asset Category"), cur),
                    "currency": cur,
                    "quantity": r.get("Quantity", "0").replace(",", ""),
                    "market_value_cad": round(float(r["Value"].replace(",", "")) * rate, 2),
                    "cost_basis_cad": round(float((r.get("Cost Basis") or r["Value"]).replace(",", "")) * rate, 2),
                })
            elif section == "Cash Report" and kind == "Header":
                cash_header = rec
            elif section == "Cash Report" and kind == "Data" and cash_header:
                r = dict(zip(cash_header, rec))
                if r.get("Currency Summary") == "Ending Cash" and r.get("Currency") not in (None, "", "Base Currency Summary"):
                    cur = r["Currency"]
                    val = float(r.get("Total", "0").replace(",", "")) * fx.get(cur, 1.0)
                    rows.append({"symbol": f"CASH.{cur}", "description": f"{cur} cash", "asset_class": "cash",
                                 "currency": cur, "quantity": 1, "market_value_cad": round(val, 2),
                                 "cost_basis_cad": round(val, 2)})
    if not rows:
        raise ValueError("No 'Open Positions' rows found. Is this an IBKR Activity Statement CSV?")
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows
