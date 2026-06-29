"""Prospectus extraction for priced IPOs.

The priced calendar tells us *what* priced and when. The prospectus on EDGAR is
the source for *everything else*: business, leadership/founder, financials,
backers, offering terms. This module resolves the right filing, slices it by
section, and reads the fields. Every printed number must trace to the filing;
where the prospectus omits a field we say "not disclosed in prospectus" rather
than guess.

Design notes:
- Resolve CIK by ticker first, then EDGAR full-text search by company name.
- Pick the prospectus by form priority 424B4 > 424B1 > 424B3 > 424B5, then the
  S-1/A (domestic) or F-1/A (foreign) family when no 424B has posted yet.
- Anchor-then-read: never scan the whole 200-page doc blindly; find the section
  heading, slice, and extract from the slice.
- Implied valuation is offer price x post-offering share count from the cover,
  never a scraped market cap. Valuations implausible vs revenue are withheld.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field

import edgar_source as edgar
import httpclient

EFTS = "https://efts.sec.gov/LATEST/search-index?q={q}"
ARCHIVES = "https://www.sec.gov/Archives"
PROSPECTUS_FORMS = ("424B4", "424B1", "424B3", "424B5",
                    "S-1/A", "S-1", "F-1/A", "F-1")
NOT_DISCLOSED = "not disclosed in prospectus"


@dataclass
class Profile:
    ticker: str = ""
    name: str = ""
    cik: str = ""
    source_form: str = ""
    accession: str = ""
    doc_url: str = ""
    foreign: bool = False
    # offering
    exchange: str = ""
    price: float | None = None
    shares_offered: float | None = None
    gross_proceeds: float | None = None
    post_offering_shares: float | None = None
    impl_valuation: float | None = None
    # narrative
    business: str = ""
    ceo_name: str = ""
    is_founder: bool | None = None
    founder_year: str = ""
    ceo_tenure: str = ""       # e.g. "March 2020" (when they became CEO)
    ceo_prior: str = ""        # prior-career snippet from the bio
    ceo_prior_source: str = "filing"   # "filing" or "web"
    cfo_name: str = ""
    founders: list[str] = field(default_factory=list)
    employees: str = ""
    external_color: str = ""   # optional web-search context (tagged external)
    # financials
    revenue: float | None = None
    revenue_period: str = ""
    revenue_prior: float | None = None
    yoy_growth: float | None = None
    gross_profit: float | None = None
    gross_margin: float | None = None
    net_income: float | None = None
    net_margin: float | None = None
    pre_revenue: bool = False
    revenue_kind: str = "revenue"
    is_bank: bool = False
    # bookkeeping
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    resolved: bool = False


# --- HTML to text ----------------------------------------------------------
def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    repl = {"&nbsp;": " ", "&#160;": " ", "&#8217;": "'", "&#8216;": "'",
            "&#8220;": '"', "&#8221;": '"', "&#8212;": ", ", "&#8211;": "-",
            "&amp;": "&", "&rsquo;": "'", "&ldquo;": '"', "&rdquo;": '"'}
    for k, v in repl.items():
        html = html.replace(k, v)
    html = re.sub(r"&#?\w+;", " ", html)
    return re.sub(r"[ \t ]+", " ", html)


# --- Money parsing ---------------------------------------------------------
def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", "").replace("$", "").strip())
    except (ValueError, AttributeError):
        return None


# --- Resolve the filing ----------------------------------------------------
def _efts_cik(name: str) -> str | None:
    clean = re.sub(r"[^\w &]", " ", name).strip()
    url = EFTS.format(q=urllib.parse.quote(f'"{clean}"'))
    try:
        data = httpclient.get_json(url, sec=True)
    except Exception:
        return None
    for hit in data.get("hits", {}).get("hits", []):
        ciks = hit.get("_source", {}).get("ciks") or []
        if ciks:
            return edgar.cik10(ciks[0])
    return None


def resolve_filing(ticker: str, name: str) -> dict | None:
    """Return {cik, form, accession, doc_url, source_tag} or None."""
    cmap = edgar._load_ticker_map()
    cik = cmap.get(ticker.upper()) or _efts_cik(name)
    if not cik:
        return None
    try:
        sub = edgar.get_submissions(cik)
    except Exception:
        return None
    rec = sub.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    accs = rec.get("accessionNumber", [])
    docs = rec.get("primaryDocument", [])
    for want in PROSPECTUS_FORMS:
        for i, f in enumerate(forms):
            if f == want and i < len(docs) and docs[i]:
                acc = accs[i]
                url = f"{ARCHIVES}/edgar/data/{int(cik)}/{acc.replace('-', '')}/{docs[i]}"
                tag = "424B4" if want.startswith("424") else want.replace("/", "")
                return {"cik": edgar.cik10(cik), "form": want, "accession": acc,
                        "doc_url": url, "source_tag": f"src:{want}"}
    return None


# --- Section slicing -------------------------------------------------------
# Major prospectus headings, used to bound a section once we have its start.
_SECTION_BREAKS = (
    "risk factors", "use of proceeds", "dividend policy", "capitalization",
    "dilution", "management's discussion", "business", "management",
    "executive compensation", "principal stockholders", "principal and selling",
    "certain relationships", "description of capital stock", "underwriting",
    "prospectus summary", "the offering", "summary consolidated financial",
    "summary financial",
)


def _section(text: str, start_patterns: tuple[str, ...], span: int = 9000) -> str:
    """Return the body of the first real (non-TOC) occurrence of a heading.

    A table-of-contents hit is followed shortly by a page number and another
    heading; a real section has prose. We take the occurrence with the most
    alphabetic content right after it.
    """
    best = ""
    best_score = 0
    low = text.lower()
    for pat in start_patterns:
        for m in re.finditer(pat, low):
            chunk = text[m.end(): m.end() + span]
            score = sum(c.isalpha() for c in chunk[:1200])
            if score > best_score:
                best_score = score
                # Trim at the next major heading.
                cut = len(chunk)
                for brk in _SECTION_BREAKS:
                    if brk in pat:
                        continue
                    bm = re.search(brk, chunk.lower())
                    if bm and 400 < bm.start() < cut:
                        cut = bm.start()
                best = chunk[:cut]
    return best


# --- Field extractors ------------------------------------------------------
_SENT = re.compile(r"(?<=[.!?])\s+")


_BOILERPLATE = re.compile(
    r"this (prospectus|summary)|you should read|before (investing|you decide)|"
    r"highlights (selected|certain)|does not contain all|together with the|"
    r"see the section|risk factors|table of contents|unless (the|we|otherwise)|"
    r"we are offering|we anticipate|no public market|initial public offering price|"
    r"obligation of dealers|deliver a prospectus|net proceeds|"
    r"emerging growth company|jobs act|jumpstart our business|smaller reporting company|"
    r"reduced public company|representation to the contrary|may have changed since|"
    r"good faith estimates|market data|industry data|results of operations may|"
    r"going concern|history of (?:net |operating )?losses|"
    r"this is the initial public offering|initial public offering of|"
    r"private placement|expect to (?:incur|continue to incur)|incurred (?:a )?net loss|"
    r"operating losses|net losses",
    re.I,
)


def _collect_sentences(start: str) -> str:
    out = []
    for s in _SENT.split(start):
        s = s.strip()
        if len(s) < 25 or _BOILERPLATE.search(s):
            continue
        out.append(s)
        if len(out) >= 4 or sum(len(x) for x in out) > 460:
            break
    return " ".join(out).strip()


_LEGAL_TAIL = re.compile(r"[,\s]+(?:Inc\.?|LLC|L\.?P\.?|PLC|plc|Ltd\.?|Limited|"
                         r"Corp\.?|Corporation|Co\.?|Company|N\.V\.|S\.A\.|AG)$", re.I)


def _name_stem(name: str) -> str:
    """First two words of the company name, legal suffix removed, for matching
    the 'X is a ...' opener (e.g. 'DPC Holdings', 'Deep Fission')."""
    n = name or ""
    prev = None
    while prev != n:                       # strip trailing legal tokens
        prev = n
        n = _LEGAL_TAIL.sub("", n).strip(" ,")
    return " ".join(n.split()[:2])


def _is_desc_lead(sentence: str, stem: str) -> bool:
    s = sentence.strip()
    if len(s) < 25 or _BOILERPLATE.search(s):
        return False
    if re.match(r"(We are|We design|We develop|We manufacture|We operate|We provide|"
                r"We build|We offer|We help|We were|At |Our (?:company|mission)\b)", s):
        return True
    if stem and stem.lower() in s[:60].lower():
        return True
    return bool(re.search(r"\bis (?:a|an|the)\b", s[:90]))


def extract_business(name: str, flat: str) -> str:
    """The company's own 'what we do', preferring the canonical opener.

    Priority: the 'Overview' sub-heading's first real sentence, then a strong
    opener ('{Company} is a ...', 'We are a/the ...', 'We design/manufacture
    ...'), skipping risk-factor / offering boilerplate.
    """
    stem = _name_stem(name)
    front = flat   # the business overview can sit well past the first 150K chars

    # 1) The "Overview" sub-heading. Its first sentence is often just the bare
    #    company name ("Deep Fission, Inc."), so scan the first few sentences for
    #    the real description lead and collect from there.
    for m in re.finditer(r"\bOverview\b\s+", front):
        sents = _SENT.split(front[m.end(): m.end() + 1600])
        for i, s in enumerate(sents[:4]):
            if _is_desc_lead(s, stem):
                res = _collect_sentences(" ".join(sents[i:]))
                if res:
                    return res
                break

    # 2) A strong company / "we" opener anywhere in the document.
    openers = [
        rf"\b{re.escape(stem)}\b[^.]{{0,30}} is (?:a|an|the)\b[^.]{{15,}}" if stem else None,
        r"\bWe are (?:a|an|the)\b[^.]{15,}",
        r"\bWe (?:design|develop|manufacture|operate|provide|build|offer|are building)\b[^.]{15,}",
        r"\bOur (?:company|mission) is\b[^.]{15,}",
    ]
    for pat in filter(None, openers):
        for m in re.finditer(pat, front):
            if _BOILERPLATE.search(m.group(0)):
                continue
            res = _collect_sentences(front[m.start():])
            if res:
                return res
    return ""


# Person-name shape: First [M.|Middle] Last, lowercase-led so it won't swallow a
# preceding sentence word or an ALLCAPS heading.
_NAME = r"[A-Z][a-z]+(?:\s+(?:[A-Z]\.|[A-Z][a-z]+)){1,2}"
_ENTITYISH = re.compile(r"\b(Inc|LLC|Corp|Ltd|PLC|Group|Holdings|Capital|"
                        r"Partners|Company|Fund|Trust|Bank)\b")
_TITLEISH = re.compile(r"\b(Chief|Officer|President|Vice|Executive|Chairman|"
                       r"Director|Founder|Senior|Financial|Operating|Medical|"
                       r"Scientific|Technology|Secretary|Treasurer|Manager)\b")


def _exec_by_title(flat: str, title: str) -> str:
    """Find the person holding a given C-suite title, e.g. 'Chief Financial Officer'."""
    for pat in (_NAME + r"\s*,[^.]{0,70}?" + title,
                r"(?:our|its)\s+(?:[A-Za-z ]{0,30}?)" + title + r"[,:\s]+(?:and\s+\w+\s+)?(" + _NAME + r")",
                title + r"[,:\s]+(?:and\s+\w+\s+)?(" + _NAME + r")"):
        for m in re.finditer(pat, flat):
            cand = (m.group(1) if m.groups() else m.group(0))
            cand = re.sub(r"\s*,.*$", "", cand).strip()
            cand = re.sub(r"\s+(?:Chief|President|Vice|Executive).*$", "", cand).strip()
            if cand and not _ENTITYISH.search(cand) and not _TITLEISH.search(cand):
                return cand
    return ""


def extract_leadership(flat: str) -> dict:
    ceo = _exec_by_title(flat, "Chief Executive Officer")
    cfo = _exec_by_title(flat, "Chief Financial Officer")
    if cfo and cfo.lower() == ceo.lower():
        cfo = ""   # a loose match picked up the CEO; drop it

    founder_year = ""
    fy = re.search(r"(?:co-)?founded\s+(?:the\s+(?:company|business)\s+)?in\s+(\d{4})", flat, re.I)
    if fy:
        founder_year = fy.group(1)

    # Founders named after "founded ... by ...".
    founders: list[str] = []
    fm = re.search(r"(?:was|were)\s+(?:co-)?founded[^.]{0,40}?\bby\b([^.]{5,240})", flat, re.I)
    if fm:
        for nm in re.finditer(_NAME, fm.group(1)):
            cand = nm.group(0).strip()
            if (not _TITLEISH.search(cand) and not _ENTITYISH.search(cand)
                    and cand.lower() not in (f.lower() for f in founders)):
                founders.append(cand)

    is_founder = None
    if ceo:
        last = re.escape(ceo.split()[-1])
        if any(ceo.lower() == f.lower() for f in founders):
            is_founder = True
        else:
            near = re.search(r"(?:found|co-found)\w*[^.]{0,140}?" + last
                             + r"|" + last + r"[^.]{0,140}?(?:found|co-found)\w*", flat, re.I)
            if near:
                is_founder = True
            elif re.search(r"\bfounder\b", flat, re.I):
                is_founder = None

    tenure, prior = _ceo_career(flat, ceo) if ceo else ("", "")
    return {"ceo": ceo, "cfo": cfo, "is_founder": is_founder,
            "founder_year": founder_year, "founders": founders,
            "tenure": tenure, "prior": prior}


# A prior-career sentence in a CEO bio, e.g. "Prior to joining the Company,
# Mr. X served as Chief Operating Officer of WElink Energy ...".
_PRIOR_SENT = re.compile(
    r"(?:Prior to joining|Prior to|Before joining|Earlier in (?:his|her) career|"
    r"Previously|also served|previously served)[^.]{15,220}\.", re.I)
_PRIOR_HEAD = re.compile(
    r"^(?:also\s+|previously\s+)?(?:Prior to joining the Company,?|Prior to[^,]*,|"
    r"Before joining[^,]*,|Earlier in (?:his|her) career,|Previously,?)?\s*"
    r"(?:Mr\.|Ms\.|Dr\.|Mrs\.)?\s*(?:[A-Z][a-z]+\s+)?"
    r"(?:also\s+)?(?:served\s+(?:as|in)\s+(?:the\s+)?|founded\s+|was\s+(?:the\s+)?)?",
    re.I)


def _ceo_career(flat: str, ceo: str) -> tuple[str, str]:
    """Return (tenure, prior) for the CEO from the Management-section bio.

    Prior career is a cleaned, trimmed sentence (reads naturally); only kept if
    it names a role and a company-like proper noun, else '' (web fallback fills).
    """
    last = re.escape(ceo.split()[-1])
    bio = flat
    for m in re.finditer(r"(?:Mr\.|Ms\.|Dr\.|Mrs\.)?\s*" + last
                         + r"\s+(?:has |currently )?(?:serves|served|founded|co-founded)", flat):
        bio = flat[m.start(): m.start() + 800]
        break

    tenure = ""
    tm = re.search(r"(?:serves|served|has served|has held|been)[^.]{0,110}?"
                   r"since\s+((?:[A-Z][a-z]+,?\s+)?\d{4})", bio, re.I)
    if tm:
        tenure = tm.group(1).strip().rstrip(",")

    prior = ""
    for pm in _PRIOR_SENT.finditer(bio):
        s = pm.group(0)
        if not re.search(r"officer|president|director|founder|chairman|partner|"
                         r"head of|advisor|managing", s, re.I):
            continue
        s = _PRIOR_HEAD.sub("", s).strip()
        s = re.sub(r"^(?:as|a|an|the)\s+", "", s, flags=re.I)
        s = re.sub(r"^(?:held |assumed )?(?:the )?role of\s+", "", s, flags=re.I)
        s = re.split(r",\s+(?:where|which|a global|the parent|responsible)", s)[0]
        s = re.sub(r"\s+", " ", s).strip().rstrip(".,")
        if not re.search(r"[A-Z][A-Za-z.&'\-]{2,}", s):   # needs a proper noun (a company)
            continue
        s = s[0].upper() + s[1:]
        prior = s if len(s) <= 130 else s[:130].rsplit(" ", 1)[0]
        break
    return tenure, prior


def extract_employees(text: str) -> str:
    m = re.search(r"we had\s+(?:approximately\s+)?([\d,]+)\s+(?:full-time\s+)?employees", text, re.I)
    if not m:
        m = re.search(r"had\s+(?:approximately\s+)?([\d,]+)\s+(?:full-time\s+)?employees", text, re.I)
    return m.group(1) if m else ""


def extract_offering(text: str, price: float | None) -> dict:
    out: dict = {"price": price, "shares_offered": None, "gross_proceeds": None,
                 "post_offering_shares": None, "exchange": ""}
    head = text[:8000]
    em = re.search(r"(NYSE|Nasdaq|NYSE American|Cboe)\b", head, re.I)
    if em:
        out["exchange"] = em.group(1).upper().replace("NASDAQ", "NASDAQ")
    so = (re.search(r"(?:we are offering|offering of)\s+([\d,]{6,})\s+(?:of our\s+)?"
                    r"(?:shares|ordinary shares|common stock|American Depositary)", head, re.I)
          or re.search(r"([\d,]{6,})\s+shares of (?:our\s+)?(?:common stock|ordinary shares|Class\s+\w)", head, re.I)
          or re.search(r"([\d,]{6,})\s+ordinary shares", head, re.I))
    if so:
        out["shares_offered"] = _num(so.group(1))
    for pat in (r"to be outstanding (?:immediately )?after this offering[^.\d]{0,40}?([\d,]{6,})",
                r"we (?:will|would) have[^.]{0,50}?([\d,]{6,})\s+(?:shares|ordinary shares)",
                r"Outstanding After (?:this |the )Offering[^.\d]{0,30}?([\d,]{6,})",
                r"([\d,]{6,})\s+(?:shares|ordinary shares)[^.]{0,60}?outstanding "
                r"(?:immediately )?(?:after|following|upon)"):
        pm = re.search(pat, text, re.I)
        if pm:
            out["post_offering_shares"] = _num(pm.group(1))
            break
    if out["price"] and out["shares_offered"]:
        out["gross_proceeds"] = out["price"] * out["shares_offered"]
    return out


_SCALE = {"thousand": 1e3, "thousands": 1e3, "million": 1e6, "millions": 1e6,
          "billion": 1e9, "billions": 1e9}


def _scale_before(flat: str, pos: int) -> float:
    seg = flat[max(0, pos - 3500):pos]
    last = None
    for m in re.finditer(r"in (thousands|millions|billions)", seg, re.I):
        last = m.group(1).lower()
    return _SCALE.get(last, 1.0)


def _money_row(flat: str, label_re: str):
    """First data row for a label: returns (numbers, position) or (None, None).

    Matches a label immediately followed by 1-4 figures (the comparison-table
    cells), most-recent-first as US filings present them.
    """
    # `(?:\(\d\)\s*)?` swallows a single-digit footnote marker like "(1)" that
    # sits between the label and the figures, so it is not read as -1.
    rx = ("(?:" + label_re + r")\s*(?:\(\d\)\s*)?[:\-]?\s*(\(?\$?\s*[\d,]+(?:\.\d+)?\)?"
          r"(?:[^A-Za-z0-9%(]{1,8}\(?\$?\s*[\d,]+(?:\.\d+)?\)?){0,3})")
    for m in re.finditer(rx, flat, re.I):
        raw = re.findall(r"\(?[\d,]+(?:\.\d+)?\)?", m.group(1))
        nums = []
        for r in raw:
            neg = r.startswith("(")
            v = _num(r.strip("()"))
            if v is not None:
                nums.append(-v if neg else v)
        if nums and nums[0] != 0:
            return nums, m.start()
    return None, None


def _is_bank(flat: str) -> bool:
    return bool(re.search(r"net interest income|noninterest income|non-interest income|"
                          r"interest income on loans|total deposits", flat, re.I))


def extract_financials(flat: str, foreign: bool, fy_max: int | None = None) -> dict:
    out: dict = {"revenue": None, "revenue_period": "", "revenue_prior": None,
                 "net_income": None, "gross_profit": None, "pre_revenue": False,
                 "revenue_kind": "revenue", "is_bank": False}
    if re.search(r"(?:have\s+)?not\s+(?:yet\s+)?generated\s+(?:any\s+)?(?:material\s+)?revenue", flat, re.I):
        out["pre_revenue"] = True

    # Plausible fiscal years only; forward-looking dates (e.g. 2035 in risk
    # factors) must not become the reported period.
    cap = fy_max if fy_max else 2100
    years = [int(y) for y in dict.fromkeys(re.findall(r"\b(20\d\d)\b", flat[:20000]))
             if 2015 <= int(y) <= cap]
    recent_year = max(years) if years else None
    if recent_year:
        out["revenue_period"] = f"FY{recent_year}"

    if out["pre_revenue"]:
        ni_nums, ni_pos = _money_row(flat, r"net loss")
        if ni_nums:
            out["net_income"] = -abs(ni_nums[0]) * _scale_before(flat, ni_pos)
        return out

    # A filing that reports in a foreign currency (e.g. RMB) is treated as
    # foreign for financials even if it priced via a 424 (form looks domestic),
    # so we never print local currency as USD.
    if re.search(r"\bRMB\b|Renminbi|\bHK\$|\bHKD\b|\bRMB|¥", flat):
        foreign = True

    # Foreign issuers report in local currency; we only print the US$ equivalent
    # when it is cleanly stated, never the local-currency number. Light touch by
    # design: if a clean US$ figure is not present, leave it "not disclosed"
    # rather than risk printing e.g. RMB as USD. Always return (no domestic
    # fallthrough that would grab the local-currency row).
    if foreign:
        # `.` (not [^.]) because the local-currency figures contain decimal
        # points; we want the US$ conversion that follows them.
        rm = re.search(r"total revenues?.{0,140}?\(US\$\s*([\d.]+)\s*(million|billion)", flat, re.I)
        if rm:
            out["revenue"] = _num(rm.group(1)) * _SCALE[rm.group(2).lower()]
            out["revenue_kind"] = "revenue (US$ equiv.)"
        nm = re.search(r"net (loss|income).{0,200}?\(US\$\s*([\d.]+)\s*(million|billion)", flat, re.I)
        if nm:
            val = _num(nm.group(2)) * _SCALE[nm.group(3).lower()]
            out["net_income"] = -val if nm.group(1).lower() == "loss" else val
        return out

    # Banks: the headline top line is total interest income (net interest income
    # plus non-interest income is the fuller measure; interest income is the
    # cleanest single row to anchor on).
    if _is_bank(flat):
        out["is_bank"] = True
        out["revenue_kind"] = "total interest income"
        nums, pos = _money_row(flat, r"total interest income")
        if nums:
            sc = _scale_before(flat, pos)
            out["revenue"] = nums[0] * sc
            if len(nums) > 1:
                out["revenue_prior"] = nums[1] * sc
        ninums, nipos = _money_row(flat, r"net income(?:\s*\(loss\))?")
        if ninums:
            out["net_income"] = ninums[0] * _scale_before(flat, nipos)
        return out

    # Domestic operating company: revenue / net sales row.
    for lab in (r"total net revenues?", r"total revenues?", r"net sales",
                r"total revenue", r"net revenues?", r"revenues?"):
        nums, pos = _money_row(flat, lab)
        if nums:
            sc = _scale_before(flat, pos)
            out["revenue"] = nums[0] * sc
            if len(nums) > 1:
                out["revenue_prior"] = nums[1] * sc
            break
    gp_nums, gp_pos = _money_row(flat, r"gross profit")
    if gp_nums:
        out["gross_profit"] = gp_nums[0] * _scale_before(flat, gp_pos)
    ni_nums, ni_pos = _money_row(flat, r"net income\s*\(loss\)|net loss|net income")
    if ni_nums:
        val = ni_nums[0] * _scale_before(flat, ni_pos)
        out["net_income"] = val
    return out


# --- Compute + validate ----------------------------------------------------
def _finalize(p: Profile) -> Profile:
    # Implied valuation for domestic only: foreign issuers trade as ADSs with a
    # ratio (e.g. 1 ADS = 20 ordinary shares), so price x ordinary-share count
    # would be wildly wrong. Foreign valuation is left "not disclosed".
    if p.price and p.post_offering_shares and not p.foreign:
        p.impl_valuation = p.price * p.post_offering_shares
    if p.revenue and p.revenue_prior:
        p.yoy_growth = (p.revenue - p.revenue_prior) / p.revenue_prior * 100
    if p.revenue and p.gross_profit is not None:
        p.gross_margin = p.gross_profit / p.revenue * 100
    if p.revenue and p.net_income is not None:
        p.net_margin = p.net_income / p.revenue * 100

    # Sanity bounds: withhold figures that almost certainly come from a mis-read
    # rather than print a number that does not trace cleanly to the filing.
    if p.revenue is not None and abs(p.revenue) < 1e6 and not p.pre_revenue:
        p.revenue = p.revenue_prior = p.yoy_growth = None  # sub-$1M "revenue" is a parse artifact
        p.gross_margin = p.net_margin = None
    if p.gross_proceeds and p.gross_proceeds > 30e9:
        p.flags.append("raise-withheld")
        p.notes.append("Raise withheld (implausible vs any real IPO; likely a share-count mis-read).")
        p.gross_proceeds = p.shares_offered = None
    # An implausibly small float (raise << implied valuation) means the
    # post-offering share count was almost certainly over-read (fully-diluted or
    # authorized rather than basic). Withhold the valuation rather than print it.
    if p.gross_proceeds and p.impl_valuation and p.gross_proceeds / p.impl_valuation < 0.03:
        p.flags.append("valuation-withheld")
        p.notes.append("Implied valuation withheld (raise is an implausibly small "
                       "share of it; post-offering share count suspect).")
        p.impl_valuation = None
    if p.impl_valuation and p.revenue and p.revenue > 0:
        if p.impl_valuation / p.revenue > 200 and p.impl_valuation > 50e9:
            p.flags.append("valuation-withheld")
            p.notes.append("Implied valuation withheld (implausible vs revenue).")
            p.impl_valuation = None
    elif p.impl_valuation and p.impl_valuation > 300e9:
        # No revenue to sanity-check against; >$300B is beyond any real IPO and
        # almost certainly a share-count mis-read.
        p.flags.append("valuation-withheld")
        p.notes.append("Implied valuation withheld (implausible, no revenue base).")
        p.impl_valuation = None
    return p


def _fin_revenue_label(fin: dict) -> str:
    if fin["pre_revenue"] and fin["revenue"] is None:
        return "pre-revenue"
    v = fin["revenue"]
    if v is None or abs(v) < 1e6:        # sub-$1M is a parse artifact, not revenue
        return "n/d"
    label = edgar._fmt_usd(v)
    if fin["is_bank"]:
        label += " (int. inc.)"
    elif fin["revenue_kind"].startswith("revenue (US$"):
        label += " (US$ eq.)"
    return label


def filer_detail(cik: str, filename: str, form: str, foreign: bool, name: str,
                 fy_max: int | None = None) -> dict:
    """Section B helper: revenue label + a short business one-liner from the
    S-1/F-1, in a single document fetch."""
    out = {"revenue": "n/d", "business": ""}
    base = edgar._accession_dir(cik, filename)
    if not base:
        return out
    try:
        idx = httpclient.get_json(f"{base}/index.json", sec=True)
    except Exception:
        return out
    doc = edgar._primary_doc(idx.get("directory", {}).get("item", []), form)
    if not doc:
        return out
    try:
        flat = re.sub(r"\s+", " ", html_to_text(httpclient.get(f"{base}/{doc}", sec=True).text))
    except Exception:
        return out
    out["revenue"] = _fin_revenue_label(extract_financials(flat, foreign or form.startswith("F-"), fy_max))
    biz = extract_business(name, flat)
    if biz:
        first = re.split(r"(?<=[.!?])\s+", biz)[0]
        out["business"] = (first[:155].rstrip() + "...") if len(first) > 158 else first
    return out


def build_profile(ticker: str, name: str, price: float | None,
                  foreign: bool = False, fy_max: int | None = None) -> Profile:
    p = Profile(ticker=ticker, name=name, price=price, foreign=foreign)
    info = resolve_filing(ticker, name)
    if not info:
        p.notes.append("No prospectus found on EDGAR.")
        return p
    p.cik, p.source_form, p.accession = info["cik"], info["form"], info["accession"]
    p.doc_url = info["doc_url"]
    # Foreign status from the actual form (F-1 family), not a name guess.
    p.foreign = info["form"].startswith("F-") or foreign
    p.resolved = True
    try:
        text = html_to_text(httpclient.get(p.doc_url, sec=True).text)
    except Exception as exc:
        p.notes.append(f"Prospectus fetch failed: {exc}")
        return p
    flat = re.sub(r"\s+", " ", text)

    off = extract_offering(flat, price)
    p.exchange = off["exchange"]
    p.shares_offered = off["shares_offered"]
    p.gross_proceeds = off["gross_proceeds"]
    p.post_offering_shares = off["post_offering_shares"]

    p.business = extract_business(name, flat)
    lead = extract_leadership(flat)
    p.ceo_name = lead["ceo"]
    p.cfo_name = lead["cfo"]
    p.is_founder = lead["is_founder"]
    p.founder_year = lead["founder_year"]
    p.founders = lead["founders"]
    p.ceo_tenure = lead["tenure"]
    p.ceo_prior = lead["prior"]
    p.employees = extract_employees(flat)

    fin = extract_financials(flat, p.foreign, fy_max)
    p.revenue = fin["revenue"]
    p.revenue_period = fin["revenue_period"]
    p.revenue_prior = fin["revenue_prior"]
    p.gross_profit = fin.get("gross_profit")
    p.net_income = fin["net_income"]
    p.pre_revenue = fin["pre_revenue"]
    p.revenue_kind = fin["revenue_kind"]
    p.is_bank = fin["is_bank"]
    return _finalize(p)
