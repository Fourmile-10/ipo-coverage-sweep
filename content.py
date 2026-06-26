"""Render the findings to text.

One place for the house style so Slack and the PDF stay consistent:
no em-dashes, no buy/sell view, no hype, every figure traceable, dates
stamped 'as of {run date}'. Founders are never invented; where leadership is
not in the calendar/enrichment sources we say so and point to the prospectus.
"""
from __future__ import annotations

from datetime import date, timedelta

import sa_source as sa
from edgar_source import Filer
from pipeline import FiledResult, PricedResult


def window_str(start: date, end: date) -> str:
    return f"{start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}"


def _flags(ipo: sa.PricedIPO) -> str:
    return f" [{', '.join(ipo.flags)}]" if ipo.flags else ""


def _price(ipo: sa.PricedIPO) -> str:
    return f"${ipo.ipo_price:g}" if ipo.ipo_price is not None else "price not disclosed"


def _exch(ipo: sa.PricedIPO) -> str:
    return ipo.exchange or "EXCH n/d"


def _clause(ipo: sa.PricedIPO) -> str:
    desc = ipo.description or ""
    if not desc:
        return ipo.industry or "business not disclosed"
    # First sentence, trimmed.
    clause = desc.split(". ")[0].strip()
    return (clause[:160] + "...") if len(clause) > 160 else clause


# --- Section A: one-liners --------------------------------------------------
def priced_one_liner(ipo: sa.PricedIPO) -> str:
    sector = ipo.sector or ipo.industry or "sector n/d"
    cap = ipo.market_cap_label if ipo.market_cap is not None else "not retrieved"
    return (
        f"{ipo.name} ({_exch(ipo)}: {ipo.ticker}), {sector}, "
        f"priced {ipo.ipo_date} at {_price(ipo)}, raise not disclosed, "
        f"mkt cap ~{cap}, {_clause(ipo)}{_flags(ipo)}"
    )


# --- Section A: deep profile ------------------------------------------------
def priced_profile(ipo: sa.PricedIPO) -> dict:
    cap = ipo.market_cap_label if ipo.market_cap is not None else "not retrieved"
    rev = ipo.revenue_label if ipo.revenue is not None else "not retrieved"
    rev_kind = "net revenue" if "crypto" in (_clause(ipo).lower()) else "revenue"
    ni = ipo.net_income_label if ipo.net_income is not None else "not retrieved"
    margin = "not disclosed"
    if ipo.net_income is not None and ipo.revenue:
        margin = f"{(ipo.net_income / ipo.revenue) * 100:+.0f}% net margin"

    summary = (
        f"{ipo.name} ({_exch(ipo)}: {ipo.ticker}) priced on {ipo.ipo_date} at "
        f"{_price(ipo)}. Leadership and founder detail were not retrieved from "
        f"the IPO calendar or enrichment sources; see the prospectus for the CEO "
        f"and whether they are the founder. What it does: {_clause(ipo)}."
    )
    stat_block = [
        ("IPO date / exchange", f"{ipo.ipo_date} / {_exch(ipo)}"),
        ("Price vs range", f"{_price(ipo)} (range not disclosed)"),
        ("Raise", "not disclosed"),
        ("Valuation at IPO", "not disclosed"),
        ("Current mkt cap", cap),
        (f"TTM {rev_kind}", rev),
        ("Net income / margin", f"{ni} ({margin})"),
        ("Employees", ipo.employees or "not disclosed"),
    ]
    bullets = [
        f"Sector / industry: {ipo.sector or 'n/d'} / {ipo.industry or 'n/d'}.",
        f"Pre-IPO investors and IPO mechanics: not disclosed in sources; see prospectus.",
    ]
    if "out-of-lane" in ipo.flags:
        bullets.append("Lane note: caught by size (>$400M cap), out of Glenn's "
                       "core lane on the business description.")
    if "sub-$400M, on-profile" in ipo.flags:
        bullets.append("Lane note: below $400M cap but on-profile for Glenn's lane.")
    bullets.append(f"Close on current TTM {rev_kind}: {rev}.")
    return {"name": ipo.name, "summary": summary, "stat_block": stat_block,
            "bullets": bullets}


# --- Section B rows ---------------------------------------------------------
def filer_row(f: Filer) -> list[str]:
    return [
        f.company,
        f.date_filed,
        f.country or ("foreign" if f.foreign else "US"),
        f.business or "not disclosed",
        f.revenue_label,
        f.offering_label,
    ]


SECTION_B_COLUMNS = ["Company", "Filed", "Country", "Business", "Revenue (FY)",
                     "Registered offering"]


# --- Slack assembly ---------------------------------------------------------
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
    lines += [f"- {priced_one_liner(i)}" for i in priced.ipos]
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
    """Plain-text deep profile for the PDF-failure fallback path."""
    prof = priced_profile(ipo)
    lines = [f"*{prof['name']}*", prof["summary"], ""]
    lines += [f"  {label}: {value}" for label, value in prof["stat_block"]]
    lines.append("")
    lines += [f"  - {b}" for b in prof["bullets"]]
    return "\n".join(lines)


def section_b_text(filed: FiledResult) -> str:
    def block(title, filers):
        if not filers:
            return f"*{title}:* none this window."
        rows = [f"  - {f.company} | filed {f.date_filed} | "
                f"{f.country or ('foreign' if f.foreign else 'US')} | "
                f"{f.business or 'not disclosed'} | rev {f.revenue_label} | "
                f"offering {f.offering_label}" for f in filers]
        return f"*{title}:*\n" + "\n".join(rows)
    return block("Newly filed, domestic (S-1)", filed.domestic) + "\n\n" + \
        block("Newly filed, foreign (F-1)", filed.foreign)


def self_audit_footer(start: date, end: date, flags: dict,
                      filings_ok: bool, priced_fallback: bool, pdf_ok: bool) -> str:
    next_sweep = (end + timedelta(days=14)).strftime("%d %b %Y")
    return (
        f"Next sweep: {next_sweep}. Sources: IPO calendar + EDGAR (index files, "
        f"424B4, submissions). Filings status: {'ok' if filings_ok else 'inconclusive'}. "
        f"Priced status: {'fallback-used' if priced_fallback else 'ok'}. "
        f"Flags: {flags['thin-float']} thin-float, {flags['cap-fallback']} cap-fallback, "
        f"{flags['out-of-lane']} out-of-lane profiled, {flags['no-EDGAR-424B4']} no-EDGAR-424B4. "
        f"PDF: {'attached' if pdf_ok else 'failed'}."
    )


def empty_line(start: date, end: date) -> str:
    return (f"IPO sweep {window_str(start, end)}: nothing priced >$100M, "
            f"no new S-1/F-1 filings.")
