"""Shared HTTP helpers.

One requests.Session, an SEC-compliant rate limiter, and retry/backoff on
429 and 5xx. SEC requests carry the descriptive User-Agent SEC requires; the
client throttles to stay under the ~10 req/s fair-access ceiling.

This module fails loud by design: after exhausting retries it raises, so a
broken source surfaces in the run instead of being swallowed.
"""
from __future__ import annotations

import time

import requests

import config

_session = requests.Session()
_last_sec_call = [0.0]  # mutable single-slot clock for the SEC throttle

# A browser-ish UA for stockanalysis (it rejects empty/odd agents); SEC calls
# override this with the descriptive SEC_USER_AGENT.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "Decade Partners Research " + config.SEC_USER_AGENT.split()[-1]
)


def _sleep_for_sec_rate() -> None:
    min_gap = 1.0 / config.SEC_MAX_RPS
    elapsed = time.monotonic() - _last_sec_call[0]
    if elapsed < min_gap:
        time.sleep(min_gap - elapsed)
    _last_sec_call[0] = time.monotonic()


def get(url: str, *, sec: bool = False, accept: str | None = None,
        tolerate: tuple[int, ...] = ()) -> requests.Response:
    """GET with retry/backoff. Set sec=True for any *.sec.gov request.

    Raises requests.HTTPError on a non-2xx that survives all retries, or the
    underlying requests exception on a network failure. 404 (and any status in
    `tolerate`) is returned to the caller, not raised, so callers can treat e.g.
    "no filings that day" as data. SEC serves 403 (not 404) for non-filing days,
    so the index fetcher passes tolerate=(403,).
    """
    benign = (404,) + tolerate
    headers = {"User-Agent": config.SEC_USER_AGENT if sec else _BROWSER_UA}
    if accept:
        headers["Accept"] = accept

    last_exc: Exception | None = None
    for attempt in range(config.HTTP_RETRIES):
        if sec:
            _sleep_for_sec_rate()
        try:
            resp = _session.get(url, headers=headers, timeout=config.HTTP_TIMEOUT)
        except requests.RequestException as exc:  # network / DNS / timeout
            last_exc = exc
            time.sleep(2 ** attempt)
            continue

        if resp.status_code in benign:
            return resp  # caller decides whether this status is benign
        if resp.status_code == 429 or resp.status_code >= 500:
            # Honour Retry-After when present, else exponential backoff.
            wait = resp.headers.get("Retry-After")
            time.sleep(float(wait) if wait else (2 ** attempt) + 1)
            last_exc = requests.HTTPError(f"{resp.status_code} for {url}")
            continue
        resp.raise_for_status()
        return resp

    raise RuntimeError(f"GET failed after {config.HTTP_RETRIES} attempts: {url}") from last_exc


def get_json(url: str, *, sec: bool = False):
    return get(url, sec=sec, accept="application/json").json()
