"""Archive the rendered PDF to the SharePoint / OneDrive folder.

SHAREPOINT_DEST is a synced local path (the OneDrive-backed
'Investing - Frameworks/IPO Coverage Sweep - Automation' folder). When it is
unset or missing, archiving is skipped and reported, never raised: the Slack
attachment is the primary delivery, this is the system-of-record copy.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import config


def archive(pdf_path: str) -> str:
    dest = config.SHAREPOINT_DEST.strip()
    if not dest:
        return "skipped (SHAREPOINT_DEST unset)"
    dest_dir = Path(dest)
    if not dest_dir.exists():
        return f"skipped (path not found: {dest})"
    target = dest_dir / Path(pdf_path).name
    shutil.copy2(pdf_path, target)
    return f"copied to {target}"
