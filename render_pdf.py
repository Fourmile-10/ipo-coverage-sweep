"""Decade-branded PDF for the IPO sweep.

Navy #1F3864 headings, mid-blue #2E5F9E accents, Arial (Helvetica fallback on
runners without the Arial TTF), US Letter, running header/footer with run date
and page numbers. The caller treats any exception here as non-fatal and falls
back to posting the findings as text.
"""
from __future__ import annotations

import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

import content

NAVY = colors.HexColor("#1F3864")
MIDBLUE = colors.HexColor("#2E5F9E")
LIGHT = colors.HexColor("#EAEFF7")
GREY = colors.HexColor("#555555")

_BASE, _BOLD = "Helvetica", "Helvetica-Bold"
for _name, _bold, _paths in (
    ("Arial", False, [r"C:\Windows\Fonts\arial.ttf", "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf"]),
    ("Arial-Bold", True, [r"C:\Windows\Fonts\arialbd.ttf", "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf"]),
):
    for _p in _paths:
        if os.path.exists(_p):
            try:
                pdfmetrics.registerFont(TTFont(_name, _p))
                if _bold:
                    _BOLD = _name
                else:
                    _BASE = _name
            except Exception:
                pass
            break


def _styles():
    ss = getSampleStyleSheet()
    out = {
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontName=_BOLD,
                             fontSize=15, textColor=NAVY, spaceBefore=14, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName=_BOLD,
                             fontSize=12, textColor=MIDBLUE, spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("body", parent=ss["BodyText"], fontName=_BASE,
                               fontSize=9.5, leading=13, alignment=TA_LEFT),
        "small": ParagraphStyle("small", parent=ss["BodyText"], fontName=_BASE,
                                fontSize=8, leading=11, textColor=GREY),
        "cell": ParagraphStyle("cell", parent=ss["BodyText"], fontName=_BASE,
                               fontSize=8, leading=10),
        "cellh": ParagraphStyle("cellh", parent=ss["BodyText"], fontName=_BOLD,
                                fontSize=8, leading=10, textColor=colors.white),
    }
    return out


def _header_footer(run_date: date):
    def draw(canvas, doc):
        canvas.saveState()
        w, h = letter
        canvas.setFillColor(NAVY)
        canvas.setFont(_BOLD, 9)
        canvas.drawString(0.75 * inch, h - 0.55 * inch, "Decade Partners | Primary Research")
        canvas.setStrokeColor(MIDBLUE)
        canvas.setLineWidth(0.75)
        canvas.line(0.75 * inch, h - 0.62 * inch, w - 0.75 * inch, h - 0.62 * inch)
        canvas.setFillColor(GREY)
        canvas.setFont(_BASE, 8)
        canvas.drawString(0.75 * inch, 0.5 * inch, f"Run {run_date.isoformat()}")
        canvas.drawRightString(w - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def render_pdf(path: str, start: date, end: date, priced, filed,
               coverage_notes: list[str], audit_line: str, run_date: date) -> str:
    st = _styles()
    doc = BaseDocTemplate(
        path, pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.9 * inch, bottomMargin=0.75 * inch,
        title=f"IPO Sweep {run_date.isoformat()}", author="Decade Partners",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame],
                                       onPage=_header_footer(run_date))])

    story = []
    story.append(Paragraph(f"IPO Coverage Sweep, {content.window_str(start, end)}", st["h1"]))
    story.append(Paragraph(f"Window {start.isoformat()} to {end.isoformat()}, inclusive. "
                           f"Market caps and TTM revenue as of {run_date.isoformat()}.",
                           st["small"]))

    # --- Section A ---
    story.append(Paragraph("Section A. Priced this window", st["h1"]))
    if not priced.ipos:
        story.append(Paragraph("Nothing priced >$100M this window.", st["body"]))
    else:
        for ipo in priced.ipos:
            story.append(Paragraph("- " + content.priced_one_liner(ipo), st["body"]))
        for ipo in priced.profiled:
            prof = content.priced_profile(ipo)
            story.append(Paragraph(prof["name"], st["h2"]))
            story.append(Paragraph(prof["summary"], st["body"]))
            story.append(Spacer(1, 4))
            story.append(_stat_grid(prof["stat_block"], st))
            story.append(Spacer(1, 4))
            for b in prof["bullets"]:
                story.append(Paragraph("- " + b, st["body"]))

    # --- Section B ---
    story.append(Paragraph("Section B. Newly filed (in registration)", st["h1"]))
    story.append(_filer_table("Domestic (S-1)", filed.domestic, st))
    story.append(Spacer(1, 8))
    story.append(_filer_table("Foreign / FPI (F-1)", filed.foreign, st))

    # --- Coverage notes ---
    story.append(Paragraph("Coverage notes", st["h2"]))
    note_rows = [[Paragraph("- " + n, st["cell"])] for n in coverage_notes] or [[Paragraph("None.", st["cell"])]]
    box = Table(note_rows, colWidths=[doc.width])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, MIDBLUE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(box)
    story.append(Spacer(1, 8))
    story.append(Paragraph("Self-audit", st["h2"]))
    story.append(Paragraph(audit_line, st["small"]))

    doc.build(story)
    return path


def _stat_grid(stat_block, st):
    cells, row = [], []
    for label, value in stat_block:
        row.append(Paragraph(f"<b>{label}</b><br/>{value}", st["cell"]))
        if len(row) == 2:
            cells.append(row)
            row = []
    if row:
        row.append(Paragraph("", st["cell"]))
        cells.append(row)
    t = Table(cells, colWidths=[3.4 * inch, 3.4 * inch])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, LIGHT),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _filer_table(title, filers, st):
    head = [Paragraph(c, st["cellh"]) for c in content.SECTION_B_COLUMNS]
    rows = [head]
    for f in filers:
        rows.append([Paragraph(str(c), st["cell"]) for c in content.filer_row(f)])
    if len(rows) == 1:
        rows.append([Paragraph("None this window.", st["cell"])]
                    + [Paragraph("", st["cell"]) for _ in range(5)])
    widths = [1.5 * inch, 0.75 * inch, 0.9 * inch, 1.9 * inch, 0.85 * inch, 1.1 * inch]
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCD6E8")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return Table([[Paragraph(title, st["h2"])], [t]], colWidths=[sum(widths)])
