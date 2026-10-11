"""The single predefined certificate template, drawn with ReportLab.

render_certificate() is a pure function: data in, PDF bytes out. It knows
nothing about the database or HTTP, so it is easy to test and to swap out.
"""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = landscape(A4)
NAVY = colors.HexColor("#1f3a5f")
GOLD = colors.HexColor("#b8860b")


def _fit_font_size(text: str, font: str, start: float, max_width: float) -> float:
    """Shrink the font until the text fits inside max_width."""
    size = start
    while size > 12 and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def render_certificate(
    *,
    recipient_name: str,
    title: str,
    issued_by: str,
    issue_date: str,
    certificate_id: str,
) -> bytes:
    buf = BytesIO()
    # pageCompression=0 keeps text readable in the raw PDF (handy for tests).
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), pageCompression=0)
    c.setTitle(f"Certificate - {recipient_name}")
    c.setAuthor(issued_by)

    # Double border
    c.setStrokeColor(NAVY)
    c.setLineWidth(6)
    c.rect(25, 25, PAGE_W - 50, PAGE_H - 50)
    c.setStrokeColor(GOLD)
    c.setLineWidth(2)
    c.rect(38, 38, PAGE_W - 76, PAGE_H - 76)

    cx = PAGE_W / 2
    max_text_w = PAGE_W - 160

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 38)
    c.drawCentredString(cx, PAGE_H - 120, "CERTIFICATE OF COMPLETION")

    c.setFillColor(colors.black)
    c.setFont("Helvetica", 16)
    c.drawCentredString(cx, PAGE_H - 180, "This is to certify that")

    name_size = _fit_font_size(recipient_name, "Helvetica-Bold", 40, max_text_w)
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", name_size)
    c.drawCentredString(cx, PAGE_H - 245, recipient_name)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(cx - 250, PAGE_H - 257, cx + 250, PAGE_H - 257)

    c.setFillColor(colors.black)
    c.setFont("Helvetica", 16)
    c.drawCentredString(cx, PAGE_H - 300, "has successfully completed")

    title_size = _fit_font_size(title, "Helvetica-Bold", 26, max_text_w)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", title_size)
    c.drawCentredString(cx, PAGE_H - 345, title)

    c.setFillColor(colors.black)
    c.setFont("Helvetica", 13)
    c.drawString(90, 100, f"Date: {issue_date}")
    c.drawRightString(PAGE_W - 90, 100, f"Issued by: {issued_by}")

    c.setFont("Helvetica", 8)
    c.setFillColor(colors.grey)
    c.drawCentredString(cx, 58, f"Certificate ID: {certificate_id}")

    c.showPage()
    c.save()
    return buf.getvalue()
