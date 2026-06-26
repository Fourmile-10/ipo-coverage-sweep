"""IPO Coverage Sweep, orchestrator.

Operating principle: fail loud, never silent. A broken run announces itself in
Slack. A 'nothing this window' line is only posted after a verified-good pull
from every source. The PDF is the goal but never blocks the alert: if findings
exist but the PDF fails, the findings post as text with a failure flag.

Usage:
  python main.py                      # scheduled run (biweekly gate applies)
  python main.py --dry-run            # build + render locally, no Slack/archive
  python main.py --force              # bypass the biweekly off-week gate
  python main.py --window-end 2026-06-26   # pin the window end (testing)
  python main.py --channel D0XXXX     # override Slack target (e.g. your DM)
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import config
import content
import pipeline
import render_pdf
import sharepoint
import slack_post
from runlog import RunLog


def _coverage_notes(log: RunLog, priced, filed) -> list[str]:
    notes = []
    for s in log.sources:
        notes.append(f"{s.name}: {'ok' if s.ok else 'ERROR'}, {s.count} rows"
                     + (f" ({s.note})" if s.note else ""))
    notes.append("Section A figures are read from each company's EDGAR prospectus "
                 "(form noted per card). Extraction is heuristic; figures that did "
                 "not trace cleanly to the filing are shown as 'not disclosed in "
                 "prospectus' and implausible raises/valuations are withheld.")
    withheld = [i.ticker for i in priced.ipos
                if "raise-withheld" in i.flags or "valuation-withheld" in i.flags]
    if withheld:
        notes.append("Figures withheld as implausible (likely a parse artifact): "
                     + ", ".join(withheld) + ".")
    nopros = [i.ticker for i in priced.ipos if "no-prospectus" in i.flags]
    if nopros:
        notes.append("No EDGAR prospectus resolved for: " + ", ".join(nopros) + ".")
    notes.append("Foreign (F-1) issuers are covered lightly: US$ equivalents are "
                 "used only where the filing states them, never local currency.")
    notes.append("Section B resale-only screening is name and reporting-history "
                 "based; a pure secondary registration that clears those checks "
                 "may still appear. Offering sizes are read from the fee exhibit "
                 "and may include resale shares.")
    if filed.days_missing:
        notes.append(f"{filed.days_missing} day(s) in the window had no EDGAR "
                     "daily index (weekends/holidays), which is expected.")
    for e in log.errors:
        notes.append("Error: " + e)
    return notes


def run(args) -> int:
    run_date = (datetime.strptime(args.window_end, "%Y-%m-%d").date()
                if args.window_end else datetime.now(config.SYDNEY_TZ).date())
    window_end = run_date
    window_start = window_end - timedelta(days=config.WINDOW_DAYS)

    # Biweekly off-week gate (scheduled runs only).
    if not args.force and not args.dry_run:
        if run_date.isocalendar().week % 2 != config.BIWEEKLY_PARITY:
            print(f"Off-week for the biweekly sweep ({run_date}); silent skip.")
            return 0

    log = RunLog(window_start, window_end)
    channel = args.channel or config.SLACK_CHANNEL_ID
    prefix = f"*{args.note}*\n\n" if args.note else ""

    # --- Pull both spines (fail loud) ---
    priced = pipeline.build_section_a(window_start, window_end, log)
    filed = pipeline.build_section_b(window_start, window_end, log)
    flags = pipeline.tally_flags(priced)
    log.flags = flags

    header = content.slack_header(window_start, window_end, priced, filed)
    coverage = _coverage_notes(log, priced, filed)
    is_empty = not priced.ipos and not filed.domestic and not filed.foreign

    # Verified-good but genuinely empty: one line only.
    if is_empty and log.all_sources_ok:
        line = prefix + content.empty_line(window_start, window_end)
        print(line)
        if not args.dry_run:
            slack_post.post_text(line, channel)
            log.slack_status = "posted empty-line"
        print(log.dump())
        return 0

    # --- Render PDF (never a blocker) ---
    pdf_ok = False
    pdf_path = str(Path(__file__).with_name(f"IPO_Sweep_{run_date.isoformat()}.pdf"))
    audit = content.self_audit_footer(
        window_start, window_end, flags, log.counts,
        filings_ok=log.all_sources_ok, pdf_ok=True,
    )
    if not args.no_pdf:
        try:
            render_pdf.render_pdf(pdf_path, window_start, window_end, priced, filed,
                                  coverage, audit, run_date)
            pdf_ok = True
            log.pdf_status = "rendered"
        except Exception as exc:
            log.pdf_status = f"failed: {exc}"
            log.error(f"PDF render failed: {exc}")
            traceback.print_exc()
    audit = content.self_audit_footer(
        window_start, window_end, flags, log.counts,
        filings_ok=log.all_sources_ok, pdf_ok=pdf_ok,
    )

    if args.dry_run:
        print(prefix + content.slack_message(window_start, window_end, priced, filed))
        print(f"\nPDF: {log.pdf_status} -> {pdf_path if pdf_ok else '(none)'}")
        print("\n--- run log ---")
        print(log.dump())
        return 0

    # --- Slack (the alert always fires) ---
    if pdf_ok:
        msg = prefix + content.slack_message(window_start, window_end, priced, filed)
        slack_post.post_with_pdf(pdf_path, msg, channel, run_date.isoformat())
        log.slack_status = "posted with PDF"
    else:
        # Degraded path: no PDF, so the detail goes into the message as text.
        parts = [prefix + header, "", content.slack_section_a_summary(priced)]
        for ipo in priced.profiled:
            parts += ["", content.profile_text(ipo)]
        parts += ["", content.section_b_text(filed), "",
                  ":warning: PDF render failed, findings posted as text.", "", audit]
        slack_post.post_text("\n".join(parts), channel)
        log.slack_status = "posted text (PDF failed)"

    # --- Archive ---
    if pdf_ok:
        try:
            log.sharepoint_status = sharepoint.archive(pdf_path)
        except Exception as exc:
            log.sharepoint_status = f"failed: {exc}"
            log.error(f"SharePoint archive failed: {exc}")

    print(log.dump())
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Decade IPO Coverage Sweep")
    p.add_argument("--dry-run", action="store_true",
                   help="Build and render locally; no Slack post, no archive.")
    p.add_argument("--force", action="store_true",
                   help="Bypass the biweekly off-week gate.")
    p.add_argument("--no-pdf", action="store_true",
                   help="Skip PDF rendering (exercises the text-fallback path).")
    p.add_argument("--window-end", default=None,
                   help="Pin the window end date YYYY-MM-DD (default: today, Sydney).")
    p.add_argument("--channel", default=None,
                   help="Override Slack channel/DM ID (default: SLACK_CHANNEL_ID).")
    p.add_argument("--note", default=None,
                   help="Prepend a bold note line to the Slack post (e.g. a test banner).")
    args = p.parse_args()

    try:
        return run(args)
    except Exception as exc:
        # Fail loud: surface the break in Slack, then re-raise for CI.
        traceback.print_exc()
        if not args.dry_run and config.SLACK_BOT_TOKEN and (args.channel or config.SLACK_CHANNEL_ID):
            try:
                today = datetime.now(config.SYDNEY_TZ).date().isoformat()
                slack_post.post_text(
                    f":rotating_light: IPO sweep failed on {today}: {exc}. "
                    f"No all-clear posted. See the run log.",
                    args.channel or config.SLACK_CHANNEL_ID,
                )
            except Exception:
                traceback.print_exc()
        raise


if __name__ == "__main__":
    sys.exit(main())
