"""Decade-branded PDF for the IPO sweep.

Navy #1F3864 headings, mid-blue #2E5F9E accents, Arial (Helvetica fallback on
runners without the Arial TTF), US Letter, running header/footer with run date
and page numbers. The caller treats any exception here as non-fatal and falls
back to posting the findings as text.
"""
from __future__ import annotations

import os
from datetime import date
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable, KeepTogether, PageTemplate, Paragraph,
    Spacer, Table, TableStyle,
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

# Map the family so the inline <b> tag resolves to the bold face. Without this,
# a TTF base font (Arial) renders <b> as regular, so labels look un-bolded.
try:
    pdfmetrics.registerFontFamily(_BASE, normal=_BASE, bold=_BOLD,
                                  italic=_BASE, boldItalic=_BOLD)
except Exception:
    pass


def _e(text) -> str:
    """Escape dynamic text so reportlab's mini-XML parser does not choke on & < >."""
    return _xml_escape(str(text)) if text is not None else ""


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
        "cardtitle": ParagraphStyle("cardtitle", parent=ss["Heading2"], fontName=_BOLD,
                                    fontSize=12.5, textColor=NAVY, spaceBefore=2, spaceAfter=0),
        "cardsub": ParagraphStyle("cardsub", parent=ss["BodyText"], fontName=_BASE,
                                  fontSize=8.5, textColor=MIDBLUE, spaceAfter=5),
        "mlabel": ParagraphStyle("mlabel", parent=ss["BodyText"], fontName=_BASE,
                                 fontSize=6.5, textColor=GREY, leading=8),
        "mval": ParagraphStyle("mval", parent=ss["BodyText"], fontName=_BOLD,
                               fontSize=9, textColor=colors.HexColor("#1A1A1A"), leading=11),
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
        story.append(_summary_table(priced.ipos, st))
        story.append(Spacer(1, 12))
        for ipo in priced.ipos:
            c = content.priced_card(ipo)
            block = [
                Paragraph(_e(c["header"]), st["cardtitle"]),
                Paragraph(_e(c["subtitle"]), st["cardsub"]),
                _metric_grid(c["metrics"], st, doc.width),
                Spacer(1, 5),
            ]
            for label, key in (("What it does", "business"),
                               ("Leadership", "leadership"),
                               ("Use of proceeds", "use_of_proceeds"),
                               ("Backers", "backers")):
                block.append(Paragraph(
                    f'<b><font color="#1F3864">{label}:</font></b> {_e(c[key])}', st["body"]))
            if c.get("external"):
                block.append(Paragraph(
                    f'<b><font color="#1F3864">Context (external):</font></b> '
                    f'<i>{_e(c["external"])}</i>', st["body"]))
            block.append(Paragraph(f"Source: {_e(c['source'])}", st["small"]))
            story.append(KeepTogether(block))
            story.append(HRFlowable(width="100%", thickness=0.4, spaceBefore=9,
                                    spaceAfter=9, color=colors.HexColor("#D7DEEC")))

    # --- Section B ---
    story.append(Paragraph("Section B. Newly filed (in registration)", st["h1"]))
    story.append(_filer_table("Domestic (S-1)", filed.domestic, st))
    story.append(Spacer(1, 8))
    story.append(_filer_table("Foreign / FPI (F-1)", filed.foreign, st))

    # --- Coverage notes ---
    story.append(Paragraph("Coverage notes", st["h2"]))
    note_rows = [[Paragraph("- " + _e(n), st["cell"])] for n in coverage_notes] or [[Paragraph("None.", st["cell"])]]
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


def _summary_table(ipos, st):
    head = [Paragraph(c, st["cellh"]) for c in content.SUMMARY_COLUMNS]
    rows = [head]
    for ipo in ipos:
        rows.append([Paragraph(_e(c), st["cell"]) for c in content.summary_row(ipo)])
    widths = [1.8 * inch, 0.75 * inch, 0.6 * inch, 0.75 * inch, 0.85 * inch,
              1.45 * inch, 0.8 * inch]
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCD6E8")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _metric_grid(metrics, st, width):
    cells, row = [], []
    for label, val in metrics:
        row.append([Paragraph(_e(label).upper(), st["mlabel"]),
                    Paragraph(_e(val), st["mval"])])
        if len(row) == 4:
            cells.append(row)
            row = []
    if row:
        while len(row) < 4:
            row.append("")
        cells.append(row)
    cw = width / 4.0
    t = Table(cells, colWidths=[cw] * 4)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCD6E8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _filer_table(title, filers, st):
    head = [Paragraph(c, st["cellh"]) for c in content.SECTION_B_COLUMNS]
    rows = [head]
    for f in filers:
        rows.append([Paragraph(_e(c), st["cell"]) for c in content.filer_row(f)])
    if len(rows) == 1:
        rows.append([Paragraph("None this window.", st["cell"])]
                    + [Paragraph("", st["cell"]) for _ in range(4)])
    widths = [1.8 * inch, 0.8 * inch, 1.0 * inch, 2.5 * inch, 0.9 * inch]
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
