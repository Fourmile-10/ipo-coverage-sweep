"""EDGAR source: the filings spine and supporting lookups.

Filings are enumerated from the daily *index files*, which need no query and
are complete. This deliberately replaces EDGAR full-text search, which during
prototyping paginated 10/page with a hard cap and silently truncated the S-1
list (200 of 563). The index files have no such cap.

  Daily master index:
    https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{q}/master.{YYYYMMDD}.idx
    Pipe-delimited: CIK|Company Name|Form Type|Date Filed|File Name

Also here: submissions / companyfacts / the ticker->CIK map / a 424B4 presence
check (used only to *confirm* priced names, never to enumerate them) / and a
best-effort offering-size read from the filing-fee exhibit.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import classify
import httpclient
import config

ARCHIVES = "https://www.sec.gov/Archives"
DAILY_INDEX = ARCHIVES + "/edgar/daily-index/{year}/QTR{q}/master.{ymd}.idx"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
TICKERS = "https://www.sec.gov/files/company_tickers.json"


@dataclass
class IndexFiling:
    cik: str
    company: str
    form: str
    date_filed: str   # YYYYMMDD
    filename: str      # edgar/data/.../accession.txt


@dataclass
class Filer:
    cik: str
    company: str
    form: str
    date_filed: str            # YYYY-MM-DD
    filename: str
    foreign: bool = False
    country: str = ""
    business: str = ""
    revenue_label: str = "n/d"
    offering_label: str = "not set"
    flags: list[str] = field(default_factory=list)


def cik10(cik: str | int) -> str:
    return str(int(cik)).zfill(10)


# --- Daily index enumeration ----------------------------------------------
def _qtr(d: date) -> int:
    return (d.month - 1) // 3 + 1


def fetch_index_filings(window_start: date, window_end: date) -> tuple[list[IndexFiling], int, int]:
    """Pull every S-1/F-1 (and /A) filing across the window from daily indexes.

    Returns (filings, days_fetched, days_missing). A missing day (404) is benign
    (weekend/holiday) and counted, not raised. The caller fails loud only if no
    day in the window returned an index at all.
    """
    filings: list[IndexFiling] = []
    days_fetched = days_missing = 0
    d = window_start
    while d <= window_end:
        url = DAILY_INDEX.format(year=d.year, q=_qtr(d), ymd=d.strftime("%Y%m%d"))
        # SEC serves 403 (not 404) for non-filing days (weekends/holidays); both
        # are benign "no index for this date", not an outage.
        resp = httpclient.get(url, sec=True, tolerate=(403,))
        if resp.status_code in (403, 404):
            days_missing += 1
            d += timedelta(days=1)
            continue
        days_fetched += 1
        for line in io.StringIO(resp.text):
            parts = line.rstrip("\n").split("|")
            if len(parts) != 5:
                continue
            cik, company, form, date_filed, filename = parts
            if form.strip() in config.FILING_FORMS:
                filings.append(
                    IndexFiling(cik.strip(), company.strip(), form.strip(),
                                date_filed.strip(), filename.strip())
                )
        d += timedelta(days=1)
    return filings, days_fetched, days_missing


# --- Submissions / reporting history --------------------------------------
def get_submissions(cik: str) -> dict:
    return httpclient.get_json(SUBMISSIONS.format(cik10=cik10(cik)), sec=True)


def _all_forms(sub: dict) -> list[str]:
    recent = sub.get("filings", {}).get("recent", {})
    return [str(f) for f in recent.get("form", [])]


def is_genuine_new_filer(cik: str, current_form: str, sub: dict) -> tuple[bool, str]:
    """Keep only first-time IPO registrants.

    Drops issuers that already report (prior 10-K/10-Q domestic; 20-F/40-F/6-K
    foreign), amendments whose original S-1/F-1 from the same CIK already
    exists, and obvious shells/funds by name. Returns (keep, reason_if_dropped).
    """
    name = sub.get("name") or ""
    sic = f"{sub.get('sic', '')} {sub.get('sicDescription', '')}"

    # Shell / SPAC / fund vehicle by name, prospectus language, or SIC 6770.
    if classify.is_spac(name, sic=sic):
        return False, "SPAC / blank-check (name or SIC 6770)"
    if classify.is_fund(name):
        return False, "fund/trust vehicle"

    forms = _all_forms(sub)
    if any(f in config.PRIOR_REPORTING_FORMS for f in forms):
        return False, "has prior periodic reporting (already public)"

    base = current_form.replace("/A", "")
    if current_form.endswith("/A"):
        # Amendment: keep only if no earlier base S-1/F-1 already filed.
        if base in forms:
            return False, "amendment of an already-registered S-1/F-1"

    return True, ""


def tag_domestic_or_foreign(form: str, sub: dict) -> tuple[bool, str]:
    foreign = form.startswith("F-")
    country = ""
    if foreign:
        country = (sub.get("stateOfIncorporationDescription")
                   or sub.get("stateOfIncorporation") or "")
    return foreign, country


def business_clause(sub: dict) -> str:
    """One-clause business description from SIC."""
    sic = sub.get("sicDescription") or ""
    return sic.strip()


# --- Revenue via XBRL company facts ---------------------------------------
def latest_annual_revenue(cik: str) -> str:
    """Most recent FY revenue from company facts, or 'n/d'.

    Many first-time filers hold financials only in the prospectus and have no
    XBRL facts yet; those correctly return 'n/d'.
    """
    try:
        facts = httpclient.get_json(COMPANYFACTS.format(cik10=cik10(cik)), sec=True)
    except Exception:
        return "n/d"
    usgaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in ("RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "SalesRevenueNet"):
        node = usgaap.get(tag)
        if not node:
            continue
        units = node.get("units", {}).get("USD", [])
        annual = [u for u in units if u.get("form") in ("10-K", "20-F", "S-1", "F-1")
                  and u.get("fp") == "FY" and u.get("val") is not None]
        if not annual:
            annual = [u for u in units if u.get("val") is not None and u.get("frame")]
        if annual:
            best = max(annual, key=lambda u: u.get("end", ""))
            return _fmt_usd(best["val"])
    return "n/d"


# --- Revenue scraped from the prospectus document -------------------------
# Genuine first-time IPO filers almost never have XBRL financial facts yet
# (companyfacts returns 0 us-gaap concepts), so the only place their revenue
# lives is the S-1/F-1 prospectus itself. We fetch the primary document, find
# the statement of operations, and read the most-recent-year total revenue.
# This is heuristic: prospectus tables vary, so we return None (caller shows
# "n/d") rather than a number we are unsure of.
_REV_LABELS = (
    r"total\s+net\s+revenues?", r"total\s+revenues?", r"total\s+net\s+sales",
    r"net\s+revenues?", r"net\s+sales", r"total\s+revenue", r"revenues?",
)


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = html.replace("&nbsp;", " ").replace("&#160;", " ").replace("&amp;", "&")
    html = re.sub(r"&#?\w+;", " ", html)
    return re.sub(r"[ \t ]+", " ", html)


def _primary_doc(items: list[dict], form: str) -> str | None:
    htm = [it for it in items
           if str(it.get("name", "")).lower().endswith((".htm", ".html"))]
    # The primary document's type equals the submission form type.
    for it in htm:
        if str(it.get("type", "")) == form:
            return it["name"]
    # Fallback: the largest .htm that is not an R-file or an exhibit.
    cand = [it for it in htm
            if not str(it["name"]).lower().startswith("r")
            and "ex" not in str(it["name"]).lower()]
    if cand:
        return max(cand, key=lambda it: int(it.get("size", 0) or 0))["name"]
    return None


def _extract_revenue(text: str) -> str | None:
    low = text.lower()
    anchor = None
    for pat in (r"consolidated\s+statements?\s+of\s+operations",
                r"statements?\s+of\s+operations",
                r"statements?\s+of\s+income"):
        m = re.search(pat, low)
        if m:
            anchor = m.start()
            break
    if anchor is None:
        return None

    window = text[anchor: anchor + 7000]
    wlow = window.lower()
    scale = 1.0
    seg = low[max(0, anchor - 1500): anchor + 1500]
    if "in thousands" in seg or "in thousands" in wlow:
        scale = 1_000.0
    elif "in millions" in seg or "in millions" in wlow:
        scale = 1_000_000.0

    # Column order = order the fiscal years appear in the table header.
    years: list[int] = []
    for y in re.findall(r"\b(20\d\d)\b", window):
        iy = int(y)
        if iy not in years:
            years.append(iy)
        if len(years) >= 4:
            break

    for lp in _REV_LABELS:
        m = re.search(lp + r"[^\d\(\)]{0,40}?(\(?\$?\s*[\d,]+(?:\.\d+)?\)?"
                      r"(?:[^\d]{1,14}\(?\$?\s*[\d,]+(?:\.\d+)?\)?){0,3})", wlow)
        if not m:
            continue
        nums = [float(n.replace(",", ""))
                for n in re.findall(r"[\d,]+(?:\.\d+)?", m.group(1))
                if n.strip(",")]
        nums = [n for n in nums if n > 0]
        if not nums:
            continue
        idx = years.index(max(years)) if years else 0  # most-recent-year column
        val = nums[idx] if idx < len(nums) else nums[0]
        val *= scale
        if 0 < val < 1e13:
            return _fmt_usd(val)
    return None


def prospectus_revenue(cik: str, filename: str, form: str) -> str | None:
    """Most-recent-FY total revenue read from the prospectus, or None."""
    base = _accession_dir(cik, filename)
    if not base:
        return None
    try:
        idx = httpclient.get_json(f"{base}/index.json", sec=True)
    except Exception:
        return None
    doc = _primary_doc(idx.get("directory", {}).get("item", []), form)
    if not doc:
        return None
    try:
        html = httpclient.get(f"{base}/{doc}", sec=True).text
    except Exception:
        return None
    return _extract_revenue(_html_to_text(html))


# --- Offering size from the filing-fee exhibit ----------------------------
_ACC_RE = re.compile(r"(\d{10}-\d{2}-\d{6})")
_MONEY_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d+)?)")


def _accession_dir(cik: str, filename: str) -> str | None:
    m = _ACC_RE.search(filename)
    if not m:
        return None
    acc = m.group(1)
    return f"{ARCHIVES}/edgar/data/{int(cik)}/{acc.replace('-', '')}"


def offering_size(cik: str, filename: str) -> str:
    """Best-effort max aggregate offering price from the fee exhibit.

    Returns a formatted dollar string, 'not set' when only the $100,000,000
    placeholder is present, or 'not disclosed' when nothing parseable is found.
    Flags resale ambiguity to the caller via the [may-include-resale] tag added
    upstream; here we just read the number.
    """
    base = _accession_dir(cik, filename)
    if not base:
        return "not disclosed"
    try:
        idx = httpclient.get_json(f"{base}/index.json", sec=True)
    except Exception:
        return "not disclosed"

    fee_doc = None
    for item in idx.get("directory", {}).get("item", []):
        nm = (item.get("name") or "").lower()
        typ = (item.get("type") or "").lower()
        if "ex-filing" in typ or "fee" in nm or "exfilingfees" in nm.replace("-", ""):
            fee_doc = item.get("name")
            break
    if not fee_doc:
        return "not disclosed"

    try:
        text = httpclient.get(f"{base}/{fee_doc}", sec=True).text
    except Exception:
        return "not disclosed"

    text_l = text.lower()
    amounts: list[float] = []
    for kw in ("maximum aggregate offering price", "aggregate offering"):
        for m in re.finditer(kw, text_l):
            tail = text[m.end():m.end() + 120]
            money = _MONEY_RE.search(tail)
            if money:
                try:
                    amounts.append(float(money.group(1).replace(",", "")))
                except ValueError:
                    pass
    amounts = [a for a in amounts if a >= 1_000_000]
    if not amounts:
        return "not disclosed"
    biggest = max(amounts)
    if abs(biggest - config.PLACEHOLDER_FEE) < 1:
        return "not set"
    return _fmt_usd(biggest)


# --- Priced confirmation: 424B4 presence ----------------------------------
_ticker_map: dict[str, str] | None = None


def _load_ticker_map() -> dict[str, str]:
    global _ticker_map
    if _ticker_map is None:
        data = httpclient.get_json(TICKERS, sec=True)
        _ticker_map = {}
        for row in data.values():
            t = str(row.get("ticker", "")).upper()
            if t:
                _ticker_map[t] = cik10(row["cik_str"])
    return _ticker_map


def has_recent_424b4(ticker: str) -> bool | None:
    """True/False if a 424B4 exists for the ticker's CIK; None if no CIK match.

    Used only to confirm priced names. A None/False here means flag
    [no-EDGAR-424B4] (the SpaceX case), never drop.
    """
    cmap = _load_ticker_map()
    cik = cmap.get(ticker.upper())
    if not cik:
        return None
    try:
        sub = get_submissions(cik)
    except Exception:
        return None
    return "424B4" in _all_forms(sub)


# --- formatting ------------------------------------------------------------
def _fmt_usd(val: float) -> str:
    val = float(val)
    if abs(val) >= 1e9:
        return f"${val / 1e9:.2f}B"
    if abs(val) >= 1e6:
        return f"${val / 1e6:.1f}M"
    return f"${val:,.0f}"
