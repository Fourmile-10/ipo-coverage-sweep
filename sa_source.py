"""stockanalysis.com source: the priced-IPO spine and per-ticker enrichment.

Two endpoints, both confirmed live at build time:

  * Recently-priced list (the priced spine, NOT EDGAR):
      https://stockanalysis.com/ipos/__data.json
    The site is SvelteKit, so the page payload is devalue-encoded (an index-
    referenced flat array). decode_node() resolves it back to plain Python.
    Rows look like: {"s": "SPCX", "n": "Space Exploration Technologies Corp.",
                     "ipoDate": "2026-06-12", "ipoPrice": 135, ...}

  * Per-symbol overview (enrichment):
      https://api.stockanalysis.com/api/symbol/s/{TICKER}/overview
    JSON with marketCap, revenue (+ revenue_type), netIncome, sharesOut,
    description, infoTable (Industry/Sector/IPO Date/Exchange/Employees),
    financialChart.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpclient

RECENT_URL = "https://stockanalysis.com/ipos/__data.json"
OVERVIEW_URL = "https://api.stockanalysis.com/api/symbol/s/{ticker}/overview"


# --- SvelteKit devalue decoding -------------------------------------------
def decode_node(node: dict):
    """Resolve one SvelteKit data node (devalue flat array) to plain Python."""
    flat = node["data"]
    memo: dict[int, object] = {}

    def resolve(i):
        if not isinstance(i, int):
            return i
        if i < 0:           # -1 undefined, -2 hole, etc.
            return None
        if i in memo:
            return memo[i]
        v = flat[i]
        if isinstance(v, dict):
            out: dict = {}
            memo[i] = out
            for k, idx in v.items():
                out[k] = resolve(idx)
            return out
        if isinstance(v, list):
            out_l: list = []
            memo[i] = out_l
            for idx in v:
                out_l.append(resolve(idx))
            return out_l
        memo[i] = v
        return v

    return resolve(0)


# --- Data model ------------------------------------------------------------
@dataclass
class PricedIPO:
    ticker: str
    name: str
    ipo_date: str          # YYYY-MM-DD
    ipo_price: float | None
    # enrichment (filled by enrich())
    market_cap: float | None = None       # USD
    market_cap_label: str = "not retrieved"
    revenue: float | None = None          # USD, TTM
    revenue_label: str = "not retrieved"
    revenue_type: str = ""
    net_income: float | None = None
    net_income_label: str = "not retrieved"
    shares_out: float | None = None
    employees: str = ""
    sector: str = ""
    industry: str = ""
    exchange: str = ""
    description: str = ""
    enriched: bool = False
    flags: list[str] = field(default_factory=list)


# --- Number parsing --------------------------------------------------------
_SUFFIX = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}


def parse_money(text) -> float | None:
    """'53.88B' -> 53880000000.0 ; '-1.59B' -> -1.59e9 ; 'n/a' -> None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().replace(",", "").replace("$", "")
    if not s or s.lower() in ("n/a", "na", "-", "n/d"):
        return None
    mult = 1.0
    if s[-1] in _SUFFIX:
        mult = _SUFFIX[s[-1]]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def fetch_recent_priced() -> list[PricedIPO]:
    """Return the recently-priced IPO list (newest first, ~200 rows)."""
    payload = httpclient.get_json(RECENT_URL)
    for node in payload.get("nodes", []):
        if node.get("type") != "data":
            continue
        decoded = decode_node(node)
        if isinstance(decoded, dict) and isinstance(decoded.get("data"), list):
            rows = decoded["data"]
            out = []
            for r in rows:
                if not isinstance(r, dict) or not r.get("s"):
                    continue
                out.append(
                    PricedIPO(
                        ticker=str(r["s"]).strip(),
                        name=str(r.get("n", "")).strip(),
                        ipo_date=str(r.get("ipoDate", "")).strip(),
                        ipo_price=parse_money(r.get("ipoPrice")),
                    )
                )
            return out
    raise RuntimeError("stockanalysis recent-IPO payload had no data node")


def _info(info_table, key: str) -> str:
    for item in info_table or []:
        if isinstance(item, dict) and item.get("t") == key:
            return str(item.get("v", "")).strip()
    return ""


def enrich(ipo: PricedIPO) -> PricedIPO:
    """Fill market cap / revenue / sector etc. from the overview endpoint.

    On failure the name is kept with 'not retrieved' labels (never dropped).
    """
    try:
        resp = httpclient.get_json(OVERVIEW_URL.format(ticker=ipo.ticker))
        data = resp.get("data") if isinstance(resp, dict) else None
        if not data:
            return ipo
    except Exception:
        return ipo

    ipo.market_cap = parse_money(data.get("marketCap"))
    if ipo.market_cap is not None:
        ipo.market_cap_label = str(data.get("marketCap"))
    ipo.revenue = parse_money(data.get("revenue"))
    if ipo.revenue is not None:
        ipo.revenue_label = str(data.get("revenue"))
    ipo.revenue_type = str(data.get("revenue_type", ""))
    ipo.net_income = parse_money(data.get("netIncome"))
    if ipo.net_income is not None:
        ipo.net_income_label = str(data.get("netIncome"))
    ipo.shares_out = parse_money(data.get("sharesOut"))
    ipo.description = str(data.get("description", "")).strip()

    info = data.get("infoTable") or []
    ipo.sector = _info(info, "Sector")
    ipo.industry = _info(info, "Industry")
    ipo.exchange = _info(info, "Stock Exchange")
    ipo.employees = _info(info, "Employees")
    ipo.enriched = True
    return ipo
