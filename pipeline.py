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
import prospectus as prospectus
import sa_source as sa
from runlog import RunLog


# --- Section A -------------------------------------------------------------
@dataclass
class PricedResult:
    ipos: list[sa.PricedIPO]
    profiled: list[sa.PricedIPO]


def _on_profile(ipo: sa.PricedIPO) -> bool:
    biz = getattr(ipo.profile, "business", "") if ipo.profile else ""
    hay = " ".join([ipo.name, ipo.sector, ipo.industry, ipo.description, biz]).lower()
    return any(h in hay for h in config.ON_PROFILE_HINTS)


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
        sa.enrich(ipo)                       # sector/exchange for lane + fallback

        # Some SPACs only reveal themselves in the prospectus language (e.g.
        # "Wilco 63 Corporation", a blank-check by description). Drop those now
        # that enrichment has given us a description.
        if classify.is_spac(ipo.name, text=ipo.description, sic=ipo.industry):
            continue

        # The prospectus on EDGAR is the source for everything printed.
        prof = prospectus.build_profile(ipo.ticker, ipo.name, ipo.ipo_price,
                                        fy_max=window_end.year)
        ipo.profile = prof
        if not prof.resolved:
            ipo.flags.append("no-prospectus")

        # Size gate from the filing: keep if the raise OR the implied valuation
        # clears $100M (a small raise on a >$100M company still counts). No
        # scraped market cap. When neither is known, keep and flag.
        size = max(prof.gross_proceeds or 0, prof.impl_valuation or 0)
        if size >= config.MIN_DEAL_SIZE:
            pass                                  # clears the line, keep
        elif prof.foreign or size == 0:
            ipo.flags.append("size-unverified")   # keep; foreign val not computed, or nothing known
        else:
            continue                              # genuinely small domestic deal, drop

        ipo.lane = "in-lane" if _on_profile(ipo) else "out-of-lane"
        ipo.flags.extend(prof.flags)
        kept.append(ipo)

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
        # XBRL first (rare for first-time filers); fall back to scraping the
        # revenue line out of the S-1/F-1 itself, which is where these issuers'
        # numbers actually live.
        rev = edgar.latest_annual_revenue(cik)
        if rev == "n/d":
            rev = prospectus.revenue_label_for(cik, f.filename, f.form,
                                               foreign_flag, window_end.year)
        filer = edgar.Filer(
            cik=cik,
            company=sub.get("name") or f.company,
            form=f.form,
            date_filed=_iso(f.date_filed),
            filename=f.filename,
            foreign=foreign_flag,
            country=country,
            business=edgar.business_clause(sub),
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
        "out-of-lane": sum(1 for i in priced.ipos if i.lane == "out-of-lane"),
        "raise-withheld": count("raise-withheld"),
        "valuation-withheld": count("valuation-withheld"),
        "size-unverified": count("size-unverified"),
        "no-prospectus": count("no-prospectus"),
    }
