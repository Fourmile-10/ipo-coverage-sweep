"""Render the findings to text.

One place for the house style so Slack and the PDF stay consistent:
no em-dashes, no buy/sell view, no hype, every figure traceable, dates
stamped 'as of {run date}'. Founders are never invented; where leadership is
not in the calendar/enrichment sources we say so and point to the prospectus.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import sa_source as sa
from edgar_source import Filer
from pipeline import FiledResult, PricedResult


def window_str(start: date, end: date) -> str:
    return f"{start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}"


NOT_DISCLOSED = "not disclosed in prospectus"


def _m(v) -> str:
    if v is None:
        return "n/d"
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:.2f}B"
    if a >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v:,.0f}"


def _price_s(ipo: sa.PricedIPO) -> str:
    return f"${ipo.ipo_price:g}" if ipo.ipo_price is not None else "n/d"


def _exch(ipo: sa.PricedIPO) -> str:
    p = ipo.profile
    return (p.exchange if p and p.exchange else (ipo.exchange or "EXCH n/d"))


# --- Section A: per-company card -------------------------------------------
def _pct(v) -> str:
    return "n/d" if v is None else f"{v:+.0f}%"


def _rev_label(p) -> str:
    if not p:
        return "Revenue"
    if p.is_bank:
        return "Interest income"
    if (p.revenue_kind or "").startswith("revenue (US$"):
        return "Revenue (US$)"
    return "Revenue"


def _rev_value(p) -> str:
    if not p:
        return "n/d"
    if p.pre_revenue and p.revenue is None:
        return "pre-revenue"
    if p.revenue is None:
        return "n/d"
    seg = _m(p.revenue)
    extra = []
    if p.revenue_period:
        extra.append(p.revenue_period)
    if p.yoy_growth is not None:
        extra.append(f"{p.yoy_growth:+.0f}% YoY")
    return seg + (f" ({', '.join(extra)})" if extra else "")


def priced_header(ipo: sa.PricedIPO) -> str:
    return f"{short_name(ipo.name)} ({_exch(ipo)}: {ipo.ticker})"


def card_subtitle(ipo: sa.PricedIPO) -> str:
    sector = ipo.sector or ipo.industry or "sector n/d"
    return f"{sector}  ·  {_exch(ipo)}  ·  priced {ipo.ipo_date}"


def card_metrics(ipo: sa.PricedIPO) -> list[tuple[str, str]]:
    """Key figures for the stat grid (label, value)."""
    p = ipo.profile
    ni = _m(p and p.net_income)
    if p and p.net_income is not None and p.net_margin is not None:
        ni = f"{_m(p.net_income)} ({p.net_margin:+.0f}%)"
    return [
        ("Offer price", _price_s(ipo)),
        ("Raise", _m(p and p.gross_proceeds)),
        ("Impl. valuation", _m(p and p.impl_valuation)),
        ("Employees", (p.employees if (p and p.employees) else "n/d")),
        (_rev_label(p), _rev_value(p)),
        ("Gross margin", _pct(p and p.gross_margin)),
        ("Net income", ni),
        ("Source", (f"{p.source_form}" if (p and p.resolved) else "no filing")),
    ]


def _leadership_line(p) -> str:
    if not p or not p.ceo_name:
        return f"CEO {NOT_DISCLOSED}"
    parts = [f"CEO {p.ceo_name}"]
    if p.is_founder is True:
        parts.append("founder" + (f" ({p.founder_year})" if p.founder_year else ""))
    elif p.is_founder is False:
        parts.append("not founder-led")
    elif p.founder_year:
        parts.append(f"founded {p.founder_year}")
    else:
        parts.append("founder n/d")
    if p.ceo_credential:
        parts.append(p.ceo_credential)
    return "; ".join(parts)


def _financials_line(p) -> str:
    if not p:
        return NOT_DISCLOSED
    bits = []
    if p.pre_revenue and p.revenue is None:
        bits.append("pre-revenue")
    elif p.revenue is not None:
        seg = f"{p.revenue_kind} {_m(p.revenue)}"
        if p.revenue_period:
            seg += f" ({p.revenue_period})"
        if p.yoy_growth is not None:
            seg += f", {p.yoy_growth:+.0f}% YoY"
        bits.append(seg)
    else:
        bits.append(f"revenue {NOT_DISCLOSED}")
    if p.gross_margin is not None:
        bits.append(f"gross margin {p.gross_margin:.0f}%")
    if p.net_income is not None:
        seg = f"net income {_m(p.net_income)}"
        if p.net_margin is not None:
            seg += f" ({p.net_margin:+.0f}% margin)"
        bits.append(seg)
    if p.employees:
        bits.append(f"{p.employees} employees")
    return "; ".join(bits)


def priced_card(ipo: sa.PricedIPO) -> dict:
    p = ipo.profile
    why = ("fits Glenn's lane (vertical SaaS / fintech / proptech / adjacency)"
           if ipo.lane == "in-lane" else "outside the core lane on the business")
    src = f"{p.source_form} {p.accession}" if (p and p.resolved) else "no EDGAR prospectus found"
    return {
        "header": priced_header(ipo),
        "subtitle": card_subtitle(ipo),
        "metrics": card_metrics(ipo),
        "business": (p.business if (p and p.business) else NOT_DISCLOSED),
        "leadership": _leadership_line(p),
        "financials": _financials_line(p),
        "backers": (", ".join(p.backers) if (p and p.backers) else NOT_DISCLOSED),
        "use_of_proceeds": (p.use_of_proceeds if (p and p.use_of_proceeds) else NOT_DISCLOSED),
        "lane": f"{ipo.lane}, {why}",
        "source": src,
    }


# --- Section A: scannable summary table ------------------------------------
SUMMARY_COLUMNS = ["Company", "Priced", "Price", "Raise", "Impl. val",
                   "Revenue", "Lane"]


def summary_row(ipo: sa.PricedIPO) -> list[str]:
    p = ipo.profile
    return [
        f"{short_name(ipo.name)} ({ipo.ticker})",
        ipo.ipo_date,
        _price_s(ipo),
        _m(p and p.gross_proceeds),
        _m(p and p.impl_valuation),
        _rev_value(p),
        ipo.lane,
    ]


# --- Section B rows ---------------------------------------------------------
def filer_row(f: Filer) -> list[str]:
    return [
        f.company,
        f.date_filed,
        f.country or ("foreign" if f.foreign else "US"),
        f.business or "not disclosed",
        f.revenue_label,
    ]


SECTION_B_COLUMNS = ["Company", "Filed", "Country", "Business", "Revenue (FY)"]


# --- Slack assembly ---------------------------------------------------------
_LEGAL_SUFFIXES = (
    ", Incorporated", " Incorporated", ", Inc.", " Inc.", ", Inc", " Inc",
    ", Corporation", " Corporation", ", Corp.", " Corp.", ", Corp", " Corp",
    ", Company", " Company", ", Ltd.", " Ltd.", ", Ltd", " Ltd",
    ", LLC", " LLC", ", L.P.", " L.P.", " PLC", " plc",
)
_EDGAR_STATE_TAG = re.compile(r"\s*/[A-Z]{2}/\s*$")  # EDGAR's "FOO BANCORP /IA/"


def short_name(name: str) -> str:
    """Trim trailing legal suffixes for a cleaner Slack read (PDF keeps full)."""
    n = _EDGAR_STATE_TAG.sub("", (name or "").strip())
    changed = True
    while changed:
        changed = False
        for suf in _LEGAL_SUFFIXES:
            if n.endswith(suf):
                n = n[: -len(suf)].rstrip(" ,")
                changed = True
    return n or (name or "")


def slack_priced_line(ipo: sa.PricedIPO) -> str:
    p = ipo.profile
    sector = ipo.sector or ipo.industry or "n/d"
    return (f"• {short_name(ipo.name)} ({_exch(ipo)}: {ipo.ticker}) — {sector}, "
            f"{_price_s(ipo)}, raise {_m(p and p.gross_proceeds)}, "
            f"impl. val {_m(p and p.impl_valuation)} [{ipo.lane}]")


def slack_message(start: date, end: date, priced: PricedResult, filed: FiledResult) -> str:
    """The clean digest that rides with the PDF. Detail lives in the PDF."""
    n_filed = len(filed.domestic) + len(filed.foreign)
    lines = [
        f"*IPO sweep · {window_str(start, end)}*",
        f"{len(priced.ipos)} priced >$100M · {n_filed} filed "
        f"({len(filed.domestic)} US, {len(filed.foreign)} foreign)",
    ]
    if priced.ipos:
        lines += ["", "*Priced*"]
        lines += [slack_priced_line(i) for i in priced.ipos]
    lines += ["", "*Filed*"]
    if filed.domestic:
        lines.append("US: " + ", ".join(short_name(f.company) for f in filed.domestic))
    if filed.foreign:
        lines.append("Foreign: " + ", ".join(short_name(f.company) for f in filed.foreign))
    if not filed.domestic and not filed.foreign:
        lines.append("None this window.")
    lines += ["", "_Full detail in the attached PDF._"]
    return "\n".join(lines)


def slack_header(start: date, end: date, priced: PricedResult, filed: FiledResult) -> str:
    return (
        f"IPO sweep, {window_str(start, end)}: "
        f"{len(priced.ipos)} priced >$100M ({len(priced.profiled)} profiled), "
        f"{len(filed.domestic) + len(filed.foreign)} newly filed "
        f"({len(filed.domestic)} domestic, {len(filed.foreign)} foreign)."
    )


def slack_section_a_summary(priced: PricedResult) -> str:
    if not priced.ipos:
        return "Section A: nothing priced >$100M this window."
    lines = ["*Section A, priced this window:*"]
    lines += [slack_priced_line(i) for i in priced.ipos]
    return "\n".join(lines)


def slack_section_b_standouts(filed: FiledResult) -> str:
    parts = []
    if filed.domestic:
        parts.append("*Newly filed, domestic (S-1):* "
                     + ", ".join(f"{f.company} ({f.date_filed})" for f in filed.domestic[:8]))
    if filed.foreign:
        parts.append("*Newly filed, foreign (F-1):* "
                     + ", ".join(f"{f.company} ({f.date_filed})" for f in filed.foreign[:8]))
    if not parts:
        parts.append("Section B: no new S-1/F-1 filings this window.")
    return "\n".join(parts)


def profile_text(ipo: sa.PricedIPO) -> str:
    """Plain-text card for the PDF-failure fallback path."""
    c = priced_card(ipo)
    return "\n".join([
        f"*{c['header']}*  {c['subtitle']}",
        f"  What it does: {c['business']}",
        f"  Leadership: {c['leadership']}",
        f"  Financials: {c['financials']}",
        f"  Use of proceeds: {c['use_of_proceeds']}",
        f"  Backers: {c['backers']}",
        f"  Lane: {c['lane']}",
        f"  Source: {c['source']}",
    ])


def section_b_text(filed: FiledResult) -> str:
    def block(title, filers):
        if not filers:
            return f"*{title}:* none this window."
        rows = [f"  - {f.company} | filed {f.date_filed} | "
                f"{f.country or ('foreign' if f.foreign else 'US')} | "
                f"{f.business or 'not disclosed'} | rev {f.revenue_label}" for f in filers]
        return f"*{title}:*\n" + "\n".join(rows)
    return block("Newly filed, domestic (S-1)", filed.domestic) + "\n\n" + \
        block("Newly filed, foreign (F-1)", filed.foreign)


def self_audit_footer(start: date, end: date, flags: dict, counts: dict,
                      filings_ok: bool, pdf_ok: bool) -> str:
    next_sweep = (end + timedelta(days=14)).strftime("%d %b %Y")
    return (
        f"Next sweep: {next_sweep}. Sources: IPO calendar + EDGAR prospectus "
        f"({counts.get('priced_via_424b4', 0)} via 424B4, {counts.get('priced_via_s1', 0)} via S-1/A, "
        f"{counts.get('priced_via_f1', 0)} via F-1) + EDGAR index files. "
        f"Filings status: {'ok' if filings_ok else 'inconclusive'}. "
        f"Flags: {flags['out-of-lane']} out-of-lane, {flags['raise-withheld']} raise-withheld, "
        f"{flags['valuation-withheld']} valuation-withheld, {flags['size-unverified']} size-unverified. "
        f"PDF: {'attached' if pdf_ok else 'failed'}."
    )


def empty_line(start: date, end: date) -> str:
    return (f"IPO sweep {window_str(start, end)}: nothing priced >$100M, "
            f"no new S-1/F-1 filings.")
