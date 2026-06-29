"""SPAC / blank-check / fund detection, shared by both spines.

SPACs name themselves many ways ("Acquisition Corp", "Acquisition IV Corp",
"Holdings XI", "Capital Corp", "Equity Partners") and some only reveal
themselves in the prospectus language or, on EDGAR, via SIC code 6770
("blank checks"). We combine name, description text and SIC so the filter holds
across all of these.
"""
from __future__ import annotations

import re

import config

# "Acquisition" / "Acquisitions" as a standalone word is, in an IPO context,
# an almost-certain SPAC tell.
_ACQ = re.compile(r"\bacquisitions?\b", re.I)
# A serial-sponsor roman numeral after a sponsor word: "Gores Holdings XI",
# "Cantor Equity Partners VII". Anchored to avoid matching "Holdings LLC".
_ROMAN = re.compile(
    r"\b(?:holdings|acquisition|capital|partners|ventures|sponsor|corporation|corp)\s+"
    r"(?:II|III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV)\b",
    re.I,
)
# Strong tells: a real operating company essentially never carries these.
_STRONG_NAME = ("blank check", "acquisition corp", "acquisition company")
# Weak tells: also worn by legitimate financials/asset managers, so these only
# count as SPAC when the issuer also has no operations (no revenue/employees).
_WEAK_NAME = ("capital corp", "equity partners")
_BLANK_CHECK_TEXT = (
    "blank check", "business combination", "effecting a merger",
    "initial business combination", "merger, amalgamation",
    "special purpose acquisition",
)


def is_spac_strong(name: str, sic: str = "") -> bool:
    """High-confidence SPAC by name or SIC 6770; safe to drop on its own."""
    n = (name or "").lower()
    if _ACQ.search(n) or _ROMAN.search(name or ""):
        return True
    if any(h in n for h in _STRONG_NAME):
        return True
    return "6770" in (sic or "") or "blank" in (sic or "").lower()


def is_spac_weak(name: str, text: str = "") -> bool:
    """Lower-confidence SPAC tells; only act on these with no-operations rescue."""
    n = (name or "").lower()
    if any(h in n for h in _WEAK_NAME):
        return True
    t = (text or "").lower()
    return any(p in t for p in _BLANK_CHECK_TEXT)


def is_spac(name: str, text: str = "", sic: str = "") -> bool:
    return is_spac_strong(name, sic) or is_spac_weak(name, text)


def is_fund(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in config.FUND_NAME_HINTS)
