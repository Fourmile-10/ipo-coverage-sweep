"""Slack delivery. The alert always fires.

If the PDF rendered: post the header + a short Section A summary + Section B
standouts and attach the PDF via files_upload_v2. If it did not: post the full
findings as text plus a PDF-failure flag. Either way, a Slack failure retries
once and then surfaces in the run log.
"""
from __future__ import annotations

import time

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import config


def _client() -> WebClient:
    if not config.SLACK_BOT_TOKEN:
        raise RuntimeError("SLACK_BOT_TOKEN must be set")
    if not config.SLACK_CHANNEL_ID:
        raise RuntimeError("SLACK_CHANNEL_ID must be set")
    return WebClient(token=config.SLACK_BOT_TOKEN)


def _retry(fn):
    try:
        return fn()
    except SlackApiError:
        time.sleep(3)
        return fn()  # second failure propagates to the caller


def post_with_pdf(pdf_path: str, text: str, channel: str, run_date_iso: str) -> None:
    client = _client()
    _retry(lambda: client.files_upload_v2(
        channel=channel,
        file=pdf_path,
        filename=f"IPO_Sweep_{run_date_iso}.pdf",
        title=f"IPO Coverage Sweep {run_date_iso}",
        initial_comment=text,
    ))


def post_text(text: str, channel: str) -> None:
    client = _client()
    _retry(lambda: client.chat_postMessage(
        channel=channel, text=text, unfurl_links=False, unfurl_media=False,
    ))
