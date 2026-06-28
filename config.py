"""Central configuration for the IPO Coverage Sweep.

All secrets are read from the environment (or a local .env in dev), never
hard-coded. See .env.example for the full list.
"""
from __future__ import annotations

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()  # no-op in CI where env vars come from GitHub Actions secrets

# --- Secrets / environment -------------------------------------------------
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")
# SEC requires a descriptive UA with contact info on every request.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "Decade Partners Research glenn@decadepartners.com.au"
)
# Optional: a local/OneDrive folder the rendered PDF is copied into for archive.
SHAREPOINT_DEST = os.environ.get("SHAREPOINT_DEST", "")

# Optional web-search enrichment (one external-context line per priced name).
# Disabled unless BOTH keys are present; the run is identical without them.
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ENRICH_MODEL = os.environ.get("ENRICH_MODEL") or "claude-haiku-4-5-20251001"
WEB_ENRICH = bool(BRAVE_API_KEY and ANTHROPIC_API_KEY)

# --- Window ----------------------------------------------------------------
WINDOW_DAYS = 14  # trailing days, inclusive of the run date
SYDNEY_TZ = ZoneInfo("Australia/Sydney")

# Biweekly gate: the sweep runs every *second* Thursday. GitHub cron cannot
# express "every two weeks", so the workflow fires every Thursday and the run
# self-skips on the off-week. Parity is taken against the ISO week number of
# the Sydney-local run date. 0 = even ISO weeks, 1 = odd ISO weeks.
# `or "0"` guards against the env var being present but empty (an unset GitHub
# Actions variable expands to ""), which would otherwise crash int("").
BIWEEKLY_PARITY = int(os.environ.get("BIWEEKLY_PARITY") or "0")

# --- Thresholds ------------------------------------------------------------
MIN_DEAL_SIZE = 100_000_000      # priced names must clear >$100M (deal or cap)
TIER_B_CAP = 400_000_000         # >$400M market cap earns a deep profile
PLACEHOLDER_FEE = 100_000_000    # the $100,000,000 S-1 fee-table placeholder

# Form types that signal a first-time registration.
FILING_FORMS = {"S-1", "S-1/A", "F-1", "F-1/A"}
# Prior periodic-reporting forms that mark an issuer as already public.
PRIOR_REPORTING_FORMS = {"10-K", "10-Q", "20-F", "40-F", "6-K"}

# Fund vehicles to exclude (SPAC/blank-check naming lives in classify.py).
FUND_NAME_HINTS = (
    " etf",
    " trust",
    " fund",
    "staked ",
    "metals trust",
    "spot ",
)

# Glenn's lane: names that earn a deep profile even below the $400M cap line.
ON_PROFILE_HINTS = (
    "software", "saas", "platform", "cloud", "data",
    "fintech", "payments", "payment", "insurtech", "insurance", "exchange",
    "bank", "lending", "credit",
    "proptech", "property", "real estate", "rental",
    "marketplace", "vertical",
)

# --- HTTP ------------------------------------------------------------------
SEC_MAX_RPS = 8           # stay under SEC's ~10 req/s fair-access ceiling
HTTP_TIMEOUT = 30
HTTP_RETRIES = 3
