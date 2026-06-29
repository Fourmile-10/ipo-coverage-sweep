"""Turn the two spines into tiered content.

Section A: priced this window (Tier A one-liners + Tier B deep profiles).
Section B: newly filed in registration (domestic S-1 / foreign F-1 tables).

All sourcing failures propagate (fail loud). Enrichment failures degrade a
single name to 'not retrieved' but never drop it or abort the run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import classify
import config
import edgar_source as edgar
import enrich_web
import prospectus as prospectus
import sa_source as sa
from runlog import RunLog


# --- Section A -------------------------------------------------------------
@dataclass
class PricedResult:
    ipos: list[sa.PricedIPO]
    profiled: list[sa.PricedIPO]


def _passes_size_gate(ipo: sa.PricedIPO, prof) -> bool:
    """Keep if raise, filing-implied valuation, OR current market cap clears
    $100M (market cap is gate-only, never printed). Foreign or all-unknown sizes
    are kept and flagged rather than dropped."""
    size = max(prof.gross_proceeds or 0, prof.impl_valuation or 0, ipo.market_cap or 0)
    if size >= config.MIN_DEAL_SIZE:
        return True
    if prof.foreign or size == 0:
        ipo.flags.append("size-unverified")
        return True
    return False                              # genuinely small domestic deal


def _web_enrich(prof, log: RunLog, ident: str) -> None:
    """Optional web enrichment (no-op unless configured): CEO prior career +
    one external-context line."""
    if not config.WEB_ENRICH:
        return
    try:
        if not prof.ceo_prior:
            bg = enrich_web.ceo_background(prof)
            if bg:
                prof.ceo_prior = bg
                prof.ceo_prior_source = "web"
        color = enrich_web.enrich(prof)
        if color:
            prof.external_color = color
    except Exception as exc:
        log.error(f"web enrichment failed for {ident}: {exc}")


def _crosscheck_424b4(window_start: date, window_end: date,
                      seen_ciks: set, log: RunLog) -> list[sa.PricedIPO]:
    """Reconcile EDGAR 424B4 (final IPO prospectus) filings against the calendar.

    Any genuine new issuer with a 424B4 in the window that the IPO calendar did
    not surface is added, flagged 'off-calendar'. Follow-on offerings (issuers
    with prior periodic reporting) and SPACs/funds are excluded.
    """
    out: list[sa.PricedIPO] = []
    try:
        filings, _, _ = edgar.fetch_index_filings(window_start, window_end, forms={"424B4"})
    except Exception as exc:
        log.error(f"424B4 cross-check failed: {exc}")
        return out

    by_cik: dict[str, edgar.IndexFiling] = {}
    for f in filings:
        by_cik.setdefault(edgar.cik10(f.cik), f)
    rev_ticker = {v: k for k, v in edgar._load_ticker_map().items()}

    added = 0
    for cik, f in by_cik.items():
        if cik in seen_ciks:
            continue
        try:
            sub = edgar.get_submissions(cik)
        except Exception:
            continue
        keep, _reason = edgar.is_genuine_new_filer(cik, "424B4", sub)
        if not keep:                          # follow-on / SPAC / fund
            continue
        prof = prospectus.build_from_filing(cik, sub.get("name") or f.company,
                                            f.filename, "424B4", window_end.year)
        if not prof.resolved:
            continue
        has_ops = bool((prof.revenue and prof.revenue > 1_000_000) or prof.employees)
        if not has_ops and classify.is_spac_weak(prof.name, text=prof.business):
            continue
        size = max(prof.gross_proceeds or 0, prof.impl_valuation or 0)
        if 0 < size < config.MIN_DEAL_SIZE and not prof.foreign:
            continue
        ipo = sa.PricedIPO(ticker=rev_ticker.get(cik, ""), name=prof.name,
                           ipo_date=_iso(f.date_filed), ipo_price=prof.price)
        ipo.profile = prof
        ipo.flags.append("off-calendar")
        ipo.flags.extend(prof.flags)
        _web_enrich(prof, log, ipo.ticker or cik)
        out.append(ipo)
        added += 1
        if added >= 25:                       # safety cap
            break
    log.counts["priced_off_calendar"] = added
    return out


def build_section_a(window_start: date, window_end: date, log: RunLog) -> PricedResult:
    rows = sa.fetch_recent_priced()           # raises on failure -> fail loud
    log.source("stockanalysis priced calendar", ok=True, count=len(rows))

    in_window = [r for r in rows
                 if r.ipo_date and window_start.isoformat() <= r.ipo_date <= window_end.isoformat()]
    # Drop only HIGH-CONFIDENCE SPACs / funds up front (cheap). Weak SPAC tells
    # ("Capital Corp", "Equity Partners", blank-check wording) are checked after
    # the prospectus, with a no-operations rescue, so real financials/asset
    # managers in-lane are not false-dropped.
    candidates = [r for r in in_window
                  if not classify.is_spac_strong(r.name) and not classify.is_fund(r.name)]

    kept: list[sa.PricedIPO] = []
    seen_ciks: set[str] = set()
    for ipo in candidates:
        sa.enrich(ipo)                       # sector/exchange + market cap (gate)

        # The prospectus on EDGAR is the source for everything printed.
        prof = prospectus.build_profile(ipo.ticker, ipo.name, ipo.ipo_price,
                                        fy_max=window_end.year)
        ipo.profile = prof
        if prof.cik:
            seen_ciks.add(prof.cik)          # so the 424B4 cross-check won't re-add
        if not prof.resolved:
            ipo.flags.append("no-prospectus")

        # Weak SPAC tell + no operations (no revenue, no employees) = a SPAC.
        # A real operating company is rescued by its revenue/employees.
        has_ops = bool((prof.revenue and prof.revenue > 1_000_000) or prof.employees)
        if not has_ops and classify.is_spac_weak(
                ipo.name, text=f"{prof.business or ''} {ipo.description or ''}"):
            continue

        if not _passes_size_gate(ipo, prof):
            continue

        _web_enrich(prof, log, ipo.ticker)
        ipo.flags.extend(prof.flags)
        kept.append(ipo)

    # Second priced source: catch a priced IPO (424B4 on EDGAR) that the
    # calendar feed missed entirely.
    kept.extend(_crosscheck_424b4(window_start, window_end, seen_ciks, log))

    # Lead with the most relevant names: largest first (implied valuation, else
    # market cap, else raise). A large tech/industrial IPO outranks a tiny
    # pre-revenue deal for a long-only quality investor.
    def _size_key(ipo: sa.PricedIPO) -> float:
        p = ipo.profile
        return max(getattr(p, "impl_valuation", 0) or 0,
                   ipo.market_cap or 0,
                   getattr(p, "gross_proceeds", 0) or 0)
    kept.sort(key=_size_key, reverse=True)

    if config.WEB_ENRICH:
        log.counts["priced_web_enriched"] = sum(
            1 for i in kept if getattr(i.profile, "external_color", ""))
    log.counts["priced_over_100m"] = len(kept)
    log.counts["priced_profiled"] = len(kept)   # every priced name now gets a card
    log.counts["priced_via_424b4"] = sum(1 for i in kept if (i.profile.source_form or "").startswith("424"))
    log.counts["priced_via_s1"] = sum(1 for i in kept if (i.profile.source_form or "").startswith("S-1"))
    log.counts["priced_via_f1"] = sum(1 for i in kept if (i.profile.source_form or "").startswith("F-1"))
    return PricedResult(ipos=kept, profiled=kept)


# --- Section B -------------------------------------------------------------
@dataclass
class FiledResult:
    domestic: list[edgar.Filer]
    foreign: list[edgar.Filer]
    days_fetched: int = 0
    days_missing: int = 0


def build_section_b(window_start: date, window_end: date, log: RunLog) -> FiledResult:
    filings, fetched, missing = edgar.fetch_index_filings(window_start, window_end)
    if fetched == 0:
        # Every weekday index failing is a real outage, not an empty window.
        raise RuntimeError("EDGAR daily index returned no days in the window")
    log.source("EDGAR daily index", ok=True, count=len(filings),
               note=f"{fetched} index days, {missing} non-filing days")

    # One representative (earliest) in-window filing per CIK.
    by_cik: dict[str, edgar.IndexFiling] = {}
    for f in filings:
        cur = by_cik.get(f.cik)
        if cur is None or f.date_filed < cur.date_filed:
            by_cik[f.cik] = f

    domestic: list[edgar.Filer] = []
    foreign: list[edgar.Filer] = []
    dropped = 0
    for cik, f in by_cik.items():
        try:
            sub = edgar.get_submissions(cik)
        except Exception as exc:
            log.error(f"submissions fetch failed for CIK {cik}: {exc}")
            continue
        keep, _reason = edgar.is_genuine_new_filer(cik, f.form, sub)
        if not keep:
            dropped += 1
            continue

        foreign_flag, country = edgar.tag_domestic_or_foreign(f.form, sub)
        company = sub.get("name") or f.company
        # One prospectus fetch gives both a real business one-liner and revenue.
        detail = prospectus.filer_detail(cik, f.filename, f.form, foreign_flag,
                                         company, window_end.year)
        # Prefer XBRL revenue when present (rare for first-time filers), else the
        # figure scraped from the filing.
        rev = edgar.latest_annual_revenue(cik)
        if rev == "n/d":
            rev = detail["revenue"]
        filer = edgar.Filer(
            cik=cik,
            company=company,
            form=f.form,
            date_filed=_iso(f.date_filed),
            filename=f.filename,
            foreign=foreign_flag,
            country=country,
            business=detail["business"] or edgar.business_clause(sub),
            revenue_label=rev,
        )
        (foreign if foreign_flag else domestic).append(filer)

    domestic.sort(key=lambda x: x.date_filed, reverse=True)
    foreign.sort(key=lambda x: x.date_filed, reverse=True)
    log.counts["filed_domestic"] = len(domestic)
    log.counts["filed_foreign"] = len(foreign)
    log.counts["filed_dropped"] = dropped
    return FiledResult(domestic, foreign, fetched, missing)


def _iso(ymd: str) -> str:
    return f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}" if len(ymd) == 8 else ymd


# --- Flag tally for the self-audit footer ----------------------------------
def tally_flags(priced: PricedResult) -> dict:
    def count(tag):
        return sum(1 for i in priced.ipos if tag in i.flags)
    return {
        "raise-withheld": count("raise-withheld"),
        "valuation-withheld": count("valuation-withheld"),
        "size-unverified": count("size-unverified"),
        "no-prospectus": count("no-prospectus"),
    }
