"""Structured run log.

Records per-source status, counts, flags and errors so a run can be audited
after the fact and so the fail-loud policy has something to report against.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date


@dataclass
class SourceStatus:
    name: str
    ok: bool = False
    count: int = 0
    note: str = ""


@dataclass
class RunLog:
    window_start: date
    window_end: date
    sources: list[SourceStatus] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    pdf_status: str = "not-attempted"
    slack_status: str = "not-attempted"
    sharepoint_status: str = "not-attempted"

    def source(self, name: str, *, ok: bool, count: int = 0, note: str = "") -> None:
        self.sources.append(SourceStatus(name, ok, count, note))

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    @property
    def all_sources_ok(self) -> bool:
        return bool(self.sources) and all(s.ok for s in self.sources)

    def to_dict(self) -> dict:
        return {
            "window": [self.window_start.isoformat(), self.window_end.isoformat()],
            "sources": [vars(s) for s in self.sources],
            "counts": self.counts,
            "flags": self.flags,
            "errors": self.errors,
            "pdf_status": self.pdf_status,
            "slack_status": self.slack_status,
            "sharepoint_status": self.sharepoint_status,
        }

    def dump(self) -> str:
        return json.dumps(self.to_dict(), indent=2)
