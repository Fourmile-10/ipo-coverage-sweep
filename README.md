# IPO Coverage Sweep

Biweekly automation for Decade Partners. Pulls newly priced and newly filed US
IPOs from public sources (no login anywhere), renders a Decade-branded PDF,
posts a clean summary plus the PDF to Slack, and archives the PDF to SharePoint.

Operating principle: **fail loud, never silent.** A broken run announces itself
in Slack. A "nothing this window" line is posted only after a verified-good pull
from every source. The PDF is the goal but never blocks the alert: if findings
exist and the PDF fails, the findings post as text with a failure flag.

## How it works

Two spines, weighted equally, deduplicated and tiered.

### A. Priced (the priced spine) — source of truth is the IPO calendar, not EDGAR
- `stockanalysis.com/ipos/__data.json` (SvelteKit devalue payload, decoded in
  `sa_source.py`) gives ticker, company, IPO date, IPO price for ~200 recent
  names. EDGAR is **not** the source of truth here: a marquee priced name
  (SpaceX) had no 424B4 on EDGAR at all, so an EDGAR-only detector would miss
  it. The 424B4 is used only to *confirm*; a calendar name with no 424B4 is
  kept and flagged `[no-EDGAR-424B4]`.
- Each ticker is enriched via the overview API (market cap, TTM revenue, sector,
  exchange). Names clear a >$100M gate on deal size, falling back to current
  market cap with `[cap-fallback]` when deal size is not exposed.

### B. Newly filed (in registration) — EDGAR index files, not full-text search
- For each date in the window, `edgar_source.py` reads the daily
  `master.{YYYYMMDD}.idx` (pipe-delimited, complete) and keeps form types
  S-1, S-1/A, F-1, F-1/A. This replaces EDGAR full-text search, which paginated
  10/page with a hard cap and silently truncated the S-1 list in prototyping.
- A genuine-new-filer test (submissions + reporting history) keeps only
  first-time registrants, drops prior-reporting issuers, amendments of an
  already-registered S-1/F-1, and SPAC/fund vehicles. Foreign issuers (F-1) are
  tagged FPI and never skipped.

### Window
Trailing 14 days, inclusive (`run_date - 14` to `run_date`). SPACs/blank-check
and funds are excluded throughout.

## Files

| File | Role |
|---|---|
| `main.py` | Orchestrator, CLI, fail-loud wrapper |
| `config.py` | Env/secrets, thresholds, name hints |
| `httpclient.py` | Shared session, SEC rate limit, retry/backoff |
| `sa_source.py` | stockanalysis priced calendar + overview enrichment |
| `edgar_source.py` | EDGAR index, submissions, companyfacts, fee exhibit, 424B4 |
| `pipeline.py` | Window filter, classify, new-filer test, tiering |
| `content.py` | House-style text (one-liners, profiles, Slack, footer) |
| `render_pdf.py` | Decade-branded PDF (reportlab) |
| `slack_post.py` | files_upload_v2 + text fallback, retry once |
| `sharepoint.py` | Archive copy to the synced OneDrive folder |

## Secrets (environment, never hard-coded)

| Var | Use |
|---|---|
| `SLACK_BOT_TOKEN` | Bot token with `files:write` + `chat:write` |
| `SLACK_CHANNEL_ID` | Target channel or DM ID |
| `SEC_USER_AGENT` | Descriptive UA SEC requires on every request |
| `SHAREPOINT_DEST` | Optional synced folder for the PDF archive |
| `BIWEEKLY_PARITY` | 0 = even ISO weeks, 1 = odd (picks the fortnight) |

## Running locally

```bash
python -m venv .venv
. .venv/Scripts/activate        # Windows (Git Bash)
pip install -r requirements.txt
cp .env.example .env            # fill in the values

# Build + render locally, no Slack, no archive:
python main.py --dry-run --window-end 2026-06-26

# Real post to your own DM (bypasses the off-week gate):
python main.py --force --channel D0XXXXXXXXX
```

## Schedule

GitHub Actions fires every Thursday 20:00 UTC (~07:00 Sydney). Off-weeks
self-skip via the `BIWEEKLY_PARITY` gate, so the net cadence is every second
Thursday. Manual runs (Actions, Run workflow) always run and accept a channel
and window-end override.
