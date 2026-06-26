# Claude Code build prompt: Decade IPO Coverage Sweep automation

Paste everything below the line into Claude Code. It is a complete build spec, including the sourcing fixes learned in prototyping (the SpaceX miss and the EDGAR full-text-search failures).

---

## Role and objective

Build a production automation called **IPO Coverage Sweep** for Decade Partners (Sydney long-only investment firm). It runs biweekly, pulls newly priced and newly filed US IPOs from public sources with no login anywhere, renders a Decade-branded PDF, posts a clean summary plus the attached PDF to a Slack channel, and archives the PDF to SharePoint. Match the conventions of the existing Decade Automation Handbook (own repo, `automation.yaml`, scheduled run, registered on the status page).

## Operating principle (non-negotiable)

Fail loud, never silent. A broken run must announce itself in Slack. A "nothing this window" message may only be posted after a verified-successful pull from every source. If any source errors or returns zero unexpectedly, post an error, not a quiet all-clear. The PDF is the goal but must never block the Slack alert: if findings exist but the PDF fails, post the findings as text and flag it.

## Stack and runtime

- Python 3.11+. Use `httpx` or `requests` with a real HTTP client (this automation has full network access, unlike the prototyping sandbox).
- Set an SEC-compliant `User-Agent` header on every SEC request, e.g. `Decade Partners Research glenn@decadepartners.com.au`. Respect SEC fair-access (max ~10 requests/second, back off on 429).
- Schedule: every second Thursday, run time configurable (default 07:00 Australia/Sydney). Use GitHub Actions cron or the handbook's scheduler.
- Secrets via environment, never hard-coded: `SLACK_BOT_TOKEN`, `SEC_USER_AGENT`, `SLACK_CHANNEL_ID`, optional `SHAREPOINT_DEST`.
- Emit a structured run log (per-source status, counts, flags, errors) and register the tool in `automation.yaml`.

## Window

- Trailing 14 days from run date, inclusive. State the exact date range at the top of every output.
- Exclude SPACs / blank-check and closed-end funds throughout.

## Data sourcing (this is the corrected design, read carefully)

Prototyping showed two failures that this build must avoid:

1. EDGAR full-text search (`efts.sec.gov/LATEST/search-index`) is unreliable for listing. An empty query returns nothing for large form types, and it paginates 10/page with a hard cap, so it silently truncated the S-1 list (captured 200 of 563). **Do not use full-text search to enumerate filings.**
2. A marquee priced IPO (SpaceX) had no 424B4 on EDGAR at all (only S-1/A and free-writing prospectuses). So no EDGAR-based "priced" detector can catch it. **EDGAR is not the source of truth for priced IPOs.**

### A. Priced IPOs (the priced spine)

- Source of truth is a live IPO calendar / recently-priced feed, not EDGAR. Use the stockanalysis IPO data (identify the working endpoint at build time, e.g. the recently-priced IPO list behind `stockanalysis.com/ipos/`; confirm the live JSON path). It returns ticker, company, IPO date, IPO price.
- Keep IPOs with an IPO date inside the window.
- Cross-check each against EDGAR 424B4 (via the index files below) as a confirmation only. If a name is on the calendar but has no EDGAR 424B4 (the SpaceX case), keep it and flag `[no-EDGAR-424B4]`, do not drop it.
- Enrich each priced ticker via `https://api.stockanalysis.com/api/symbol/s/{TICKER}/overview` (JSON: marketCap, revenue TTM, netIncome, sharesOut, description, infoTable with Industry/Sector/IPO Date/IPO Price/Exchange, financialChart). If enrichment fails, keep the name with "mkt cap / TTM rev: not retrieved".
- Keep priced names with deal size > $100M; if deal size is unavailable, fall back to current market cap > $100M and flag `[cap-fallback]`.

### B. Newly filed (in registration)

- Enumerate filings from the EDGAR index files, which need no query and are complete. For each date in the window, fetch the daily master index:
  `https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{q}/master.{YYYYMMDD}.idx`
  (pipe-delimited: CIK|Company|Form Type|Date Filed|Filename). Filter Form Type in {S-1, S-1/A, F-1, F-1/A}. This replaces full-text search and fixes the truncation.
- Run both channels and weight them equally. The F-1 channel is the foreign-issuer path and must not be skipped (foreign names like Bending Spoons register on F-1, not S-1).

### Genuine-new-filer test (only first-time IPO registrants)

For each candidate CIK, pull `https://data.sec.gov/submissions/CIK{10-digit}.json` AND cross-check the index/filing history (do not trust the submissions feed alone, it lagged on SpaceX). Drop the issuer if any is true:

- It has prior periodic reporting (already public, so this is a resale or follow-on): domestic prior 10-K or 10-Q; foreign prior 20-F, 40-F, or 6-K.
- The current filing is an amendment (S-1/A or F-1/A) and an earlier S-1/F-1 from the same CIK already exists. Keep only the first public registration per issuer.
- Shell or SPAC (name contains "Acquisition Corp", or blank-check business) or a fund vehicle (ETF, Trust, Fund, "Staked", "Metals Trust").
- Pure secondary/resale registration (selling-shareholder shares only, no primary raise).

Keep only issuers with no prior periodic reporting. Tag each kept filer domestic or foreign/FPI.

### Per-filer detail

For each kept filer capture: company name, domestic/foreign, country (state of incorporation for foreign), form type, filing date, one-clause business (from the filing / SIC), most recent fiscal-year revenue if available (try the XBRL companyfacts API `https://data.sec.gov/api/xbrl/companyfacts/CIK{10-digit}.json`; many first-time filers hold financials only in the prospectus, mark "n/d" then), and registered offering size from the filing-fee exhibit (the max aggregate amount; ignore the $100,000,000 placeholder, mark "not set" if only the placeholder is present; flag amounts that may include resale shares).

## Validation

- Match company name between the calendar/EDGAR and the enrichment API to kill ticker collisions.
- Flag `[thin-float]` runners (tiny raise pumped to a large nominal cap on ~0 revenue). Do not deep-profile these.
- Crypto/custody names: treat headline "revenue" as gross throughput, use net revenue and say so.

## Tiering and content

### Section A, priced this window

- Tier A one-liner for every priced IPO > $100M:
  `Company (EXCH: TICKER), [sector], priced {date} at ${price}, raised ${deal}M, mkt cap ~${cap}, {one clause on what it does} + [flags]`
- Tier B deep profile if either (a) current market cap > $400M, or (b) market cap < $400M but the name fits Glenn's profile (vertical SaaS; fintech/payments/insurtech/exchanges; proptech; clear adjacency). A name caught only by (a) but out-of-lane: still profile, add one clause why it is out-of-lane. A name caught only by (b): flag `[sub-$400M, on-profile]`. Do not deep-profile thin-float names.
- Deep profile shape: a summary paragraph that leads on the CEO and whether they are the founder (do not invent founders; if sponsor-controlled or externally-managed, say so), then a quick financial-profile stat block (IPO date/exchange, price vs range, raise, valuation, current mkt cap, TTM revenue, net income/margin, employees), then key-point bullets (founding, pre-IPO investors, IPO mechanics, growth, lane note, close on current TTM revenue).

### Section B, newly filed (in registration)

- Two grouped tables, Domestic (S-1) and Foreign/FPI (F-1), newest filing first. Columns: Company, Filed, Country, Business, Revenue (FY), Registered offering. No inline bracket tags; the grouping carries the type.

## Style (non-negotiable)

- No em-dashes anywhere. Use commas, semicolons, parentheses, periods.
- No buy/sell view, no "this signals", no hype. Just what they are and the numbers.
- Every figure traces to EDGAR, the filing, the IPO calendar, or the enrichment pull. If unsure, write "not disclosed."
- Date everything: market caps and TTM revenue "as of {run date}."

## PDF (bonus, never a blocker)

- Decade brand: navy `#1F3864` headings, mid-blue `#2E5F9E` accents, Arial, US Letter, header "Decade Partners | Primary Research", footer with run date and page numbers. Filename `IPO_Sweep_{YYYY-MM-DD}.pdf`.
- Layout: Section A one-liners then deep profiles (summary, stat grid, bullets), then Section B grouped tables, then a coverage-notes box (any source caveats), then a self-audit line.
- If the PDF step fails for any reason, do not abort: post findings as text and append a PDF-failure flag.

## Delivery, Slack (the alert always fires)

- Use a Slack bot token with `files:write` and `chat:write`. Post to `SLACK_CHANNEL_ID` (Glenn's DM or a research channel).
- Header: `IPO sweep, {date range}: {P} priced >$100M ({M} profiled), {F} newly filed ({Fd} domestic, {Ff} foreign).`
- If the PDF rendered: post header + a short Section A summary + Section B standouts, and attach the PDF via `files.uploadV2`.
- If the PDF failed: post header + full Section A (one-liners + profiles) + Section B as text, plus the PDF-failure flag.
- Verified-good but genuinely empty: post one line, `IPO sweep {date range}: nothing priced >$100M, no new S-1/F-1 filings.`
- If the Slack post itself fails: retry once, then surface the failure in the run log.
- Also archive the PDF to SharePoint at `Investing - Frameworks / IPO Coverage Sweep - Automation`.

## Self-audit footer (end of Slack post)

`Next sweep: {date+14d}. Sources: IPO calendar + EDGAR (index files, 424B4, submissions). Filings status: {ok / inconclusive}. Priced status: {ok / fallback-used}. Flags: {n} thin-float, {n} cap-fallback, {n} out-of-lane profiled, {n} no-EDGAR-424B4. PDF: {attached / failed}.`

## Acceptance criteria (test before shipping)

- Re-run against the 12 to 26 Jun 2026 window and confirm: SpaceX is caught as priced via the IPO calendar (not via EDGAR); the filed list from the index files is complete (no full-text-search truncation); SK hynix and Bending Spoons appear on the F-1 side; SPACs and resales are dropped; the run fails loud if any source errors.
- Confirm the Slack post attaches the PDF and renders cleanly on mobile (no Microsoft login needed, since the file is native to Slack).
- Confirm secrets are read from environment, not hard-coded, and the run log records per-source status.
