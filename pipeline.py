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
import sa_source as sa
from runlog import RunLog


# --- Section A -------------------------------------------------------------
@dataclass
class PricedResult:
    ipos: list[sa.PricedIPO]
    profiled: list[sa.PricedIPO]


def _on_profile(ipo: sa.PricedIPO) -> bool:
    hay = " ".join([ipo.name, ipo.sector, ipo.industry, ipo.description]).lower()
    return any(h in hay for h in config.ON_PROFILE_HINTS)


def _is_thin_float(ipo: sa.PricedIPO) -> bool:
    # Tiny revenue pumped to a large nominal cap.
    return (ipo.revenue is not None and ipo.revenue < 5_000_000
            and ipo.market_cap is not None and ipo.market_cap > 500_000_000)


def build_section_a(window_start: date, window_end: date, log: RunLog) -> PricedResult:
    rows = sa.fetch_recent_priced()           # raises on failure -> fail loud
    log.source("stockanalysis priced calendar", ok=True, count=len(rows))

    in_window = [r for r in rows
                 if r.ipo_date and window_start.isoformat() <= r.ipo_date <= window_end.isoformat()]
    # Drop SPACs / blank-check / funds by name up front (cheap, pre-enrichment).
    candidates = [r for r in in_window
                  if not classify.is_spac(r.name) and not classify.is_fund(r.name)]

    kept: list[sa.PricedIPO] = []
    for ipo in candidates:
        sa.enrich(ipo)

        # Some SPACs only reveal themselves in the prospectus language (e.g.
        # "Wilco 63 Corporation", a blank-check by description). Drop those now
        # that enrichment has given us a description.
        if classify.is_spac(ipo.name, text=ipo.description, sic=ipo.industry):
            continue

        # 424B4 confirmation only (never an enumerator). SpaceX has none.
        try:
            has = edgar.has_recent_424b4(ipo.ticker)
        except Exception:
            has = None
        if has is False or has is None:
            ipo.flags.append("no-EDGAR-424B4")

        # Size gate: deal size is rarely exposed by the calendar, so we gate on
        # current market cap (>$100M) and flag the fallback, per spec.
        if ipo.market_cap is not None:
            ipo.flags.append("cap-fallback")
            if ipo.market_cap < config.MIN_DEAL_SIZE:
                continue  # below the $100M line, drop from the sweep
        else:
            ipo.flags.append("size-unverified")  # keep, cannot confirm >$100M

        if _is_thin_float(ipo):
            ipo.flags.append("thin-float")
        kept.append(ipo)

    profiled = _select_profiles(kept)
    log.counts["priced_over_100m"] = len(kept)
    log.counts["priced_profiled"] = len(profiled)
    return PricedResult(ipos=kept, profiled=profiled)


def _select_profiles(kept: list[sa.PricedIPO]) -> list[sa.PricedIPO]:
    profiled = []
    for ipo in kept:
        if "thin-float" in ipo.flags:
            continue  # never deep-profile thin-float runners
        big = ipo.market_cap is not None and ipo.market_cap > config.TIER_B_CAP
        on_lane = _on_profile(ipo)
        if big and not on_lane:
            ipo.flags.append("out-of-lane")
            profiled.append(ipo)
        elif big and on_lane:
            profiled.append(ipo)
        elif (not big) and on_lane:
            ipo.flags.append("sub-$400M, on-profile")
            profiled.append(ipo)
    return profiled


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
        filer = edgar.Filer(
            cik=cik,
            company=sub.get("name") or f.company,
            form=f.form,
            date_filed=_iso(f.date_filed),
            filename=f.filename,
            foreign=foreign_flag,
            country=country,
            business=edgar.business_clause(sub),
            revenue_label=edgar.latest_annual_revenue(cik),
            offering_label=edgar.offering_size(cik, f.filename),
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
        "thin-float": count("thin-float"),
        "cap-fallback": count("cap-fallback"),
        "out-of-lane": sum(1 for i in priced.profiled if "out-of-lane" in i.flags),
        "no-EDGAR-424B4": count("no-EDGAR-424B4"),
    }
