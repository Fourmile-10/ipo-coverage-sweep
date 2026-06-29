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


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def pretty_date(iso: str) -> str:
    """'2026-06-26' -> '26th June'."""
    try:
        d = date.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso or "n/d"
    return f"{_ordinal(d.day)} {d.strftime('%B')}"


NOT_DISCLOSED = "not disclosed in prospectus"


def _m(v) -> str:
    if v is None:
        return "n/d"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{sign}${a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M"
    return f"{sign}${a:,.0f}"


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
    if ipo.ticker:
        return f"{short_name(ipo.name)} ({_exch(ipo)}: {ipo.ticker})"
    return f"{short_name(ipo.name)} ({_exch(ipo)})"


def card_subtitle(ipo: sa.PricedIPO) -> str:
    return ipo.sector or ipo.industry or "Sector not disclosed"


def deal_stats(ipo: sa.PricedIPO) -> list[tuple[str, str]]:
    """Right-panel 'Deal' rows."""
    p = ipo.profile
    rows = [
        ("Priced", pretty_date(ipo.ipo_date) if ipo.ipo_date else "n/d"),
        ("Offer price", _price_s(ipo)),
        ("Raise", _m(p and p.gross_proceeds)),
        ("Impl. valuation", _m(p and p.impl_valuation)),
    ]
    if ipo.current_price is not None:
        rows.append(("Current price", f"${ipo.current_price:g}"))
    if ipo.return_pct is not None:
        rows.append(("Return since IPO", f"{ipo.return_pct:+.0f}%"))
    return rows


def fin_stats(ipo: sa.PricedIPO) -> list[tuple[str, str]]:
    """Right-panel 'Financials' rows."""
    p = ipo.profile
    rows = [(_rev_label(p), _rev_value(p))]
    if p and p.gross_margin is not None:
        rows.append(("Gross margin", f"{p.gross_margin:.0f}%"))
    rows.append(("Net income", _m(p and p.net_income)))
    if p and p.net_margin is not None:
        rows.append(("Net margin", f"{p.net_margin:+.0f}%"))
    rows.append(("Employees", (p.employees if (p and p.employees) else "n/d")))
    return rows


def _leadership_line(p) -> str:
    if not p or not p.ceo_name:
        return f"CEO {NOT_DISCLOSED}"
    # CEO with founder status or tenure ("how long at the business").
    ceo = f"CEO {p.ceo_name}"
    if p.is_founder is True:
        ceo += " (founder" + (f", {p.founder_year}" if p.founder_year else "") + ")"
    elif p.ceo_tenure:
        ceo += f" (CEO since {p.ceo_tenure})"
    lead = [ceo]
    if p.cfo_name:
        lead.append(f"CFO {p.cfo_name}")
    line = "; ".join(lead) + "."

    extras = []
    # Prior career ("where else they have been").
    if p.ceo_prior:
        prior = p.ceo_prior.rstrip(".")
        prior = prior[0].upper() + prior[1:]
        tag = " [web]" if p.ceo_prior_source == "web" else ""
        extras.append(f"Previously {prior}{tag}")
    others = [f for f in (p.founders or []) if f.lower() != p.ceo_name.lower()]
    if others:
        extras.append("co-founders " + ", ".join(others))
    if extras:
        line += " " + ". ".join(extras) + "."
    return line


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


def _fin_sentence(p) -> str:
    """A short financials clause woven into the description prose."""
    if not p:
        return ""
    if p.pre_revenue and p.revenue is None:
        if p.net_income is not None:
            return f"Pre-revenue, with a net loss of {_m(abs(p.net_income))}."
        return "Pre-revenue."
    if p.revenue is None:
        return ""
    label = "interest income" if p.is_bank else "revenue"
    us = " (US$ equiv.)" if (p.revenue_kind or "").startswith("revenue (US$") else ""
    lead = (f"{p.revenue_period} " if p.revenue_period else "") + \
        f"{label} {_m(p.revenue)}{us}"
    if p.yoy_growth is not None:
        lead += f", up {p.yoy_growth:.0f}%" if p.yoy_growth >= 0 else f", down {abs(p.yoy_growth):.0f}%"
    extra = []
    if p.gross_margin is not None:
        extra.append(f"{p.gross_margin:.0f}% gross margin")
    if p.net_income is not None:
        seg = f"net income {_m(p.net_income)}"
        if p.net_margin is not None:
            seg += f" ({p.net_margin:+.0f}% margin)"
        extra.append(seg)
    s = lead + ("; " + "; ".join(extra) if extra else "")
    return s[0].upper() + s[1:] + "."


def business_paragraph(ipo: sa.PricedIPO) -> str:
    """Description in the house style: founding, what it does, and the headline
    financials woven into the prose."""
    p = ipo.profile
    parts = []
    if p and p.founder_year:
        founded = f"Founded {p.founder_year}"
        if p.hq:
            founded += f" in {p.hq}"
        parts.append(founded + ".")
    biz = (p.business if (p and p.business) else NOT_DISCLOSED)
    parts.append(biz if biz.endswith((".", "!", "?")) else biz + ".")
    fin = _fin_sentence(p)
    if fin:
        parts.append(fin)
    return " ".join(parts)


def priced_card(ipo: sa.PricedIPO) -> dict:
    p = ipo.profile
    src = f"{p.source_form} {p.accession}" if (p and p.resolved) else "no EDGAR prospectus found"
    return {
        "header": priced_header(ipo),
        "name": short_name(ipo.name),
        "ticker": ipo.ticker,
        "exch": _exch(ipo),
        "subtitle": card_subtitle(ipo),
        "deal_stats": deal_stats(ipo),
        "fin_stats": fin_stats(ipo),
        "business": business_paragraph(ipo),
        "leadership": _leadership_line(p),
        "financials": _financials_line(p),
        "external": (p.external_color if (p and p.external_color) else ""),
        "source": src,
    }


# --- Section A: scannable summary table ------------------------------------
SUMMARY_COLUMNS = ["Company", "Priced", "Price", "Raise", "Impl. val", "Revenue"]


def summary_row(ipo: sa.PricedIPO) -> list[str]:
    p = ipo.profile
    return [
        f"{short_name(ipo.name)} ({ipo.ticker})" if ipo.ticker else short_name(ipo.name),
        pretty_date(ipo.ipo_date),
        _price_s(ipo),
        _m(p and p.gross_proceeds),
        _m(p and p.impl_valuation),
        _rev_value(p),
    ]


# --- Section B rows ---------------------------------------------------------
def filer_row(f: Filer) -> list[str]:
    return [
        f.company,
        pretty_date(f.date_filed),
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
            f"impl. val {_m(p and p.impl_valuation)}")


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
                     + ", ".join(f"{f.company} ({pretty_date(f.date_filed)})" for f in filed.domestic[:8]))
    if filed.foreign:
        parts.append("*Newly filed, foreign (F-1):* "
                     + ", ".join(f"{f.company} ({pretty_date(f.date_filed)})" for f in filed.foreign[:8]))
    if not parts:
        parts.append("Section B: no new S-1/F-1 filings this window.")
    return "\n".join(parts)


def profile_text(ipo: sa.PricedIPO) -> str:
    """Plain-text card for the PDF-failure fallback path."""
    c = priced_card(ipo)
    lines = [
        f"*{c['header']}*  {c['subtitle']}",
        f"  What it does: {c['business']}",
        f"  Leadership: {c['leadership']}",
        f"  Financials: {c['financials']}",
    ]
    if c.get("external"):
        lines.append(f"  Context (external): {c['external']}")
    lines.append(f"  Source: {c['source']}")
    return "\n".join(lines)


def section_b_text(filed: FiledResult) -> str:
    def block(title, filers):
        if not filers:
            return f"*{title}:* none this window."
        rows = [f"  - {f.company} | filed {pretty_date(f.date_filed)} | "
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
        f"Flags: {flags['raise-withheld']} raise-withheld, "
        f"{flags['valuation-withheld']} valuation-withheld, {flags['size-unverified']} size-unverified. "
        f"PDF: {'attached' if pdf_ok else 'failed'}."
    )


def empty_line(start: date, end: date) -> str:
    return (f"IPO sweep {window_str(start, end)}: nothing priced >$100M, "
            f"no new S-1/F-1 filings.")
