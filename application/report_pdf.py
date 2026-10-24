import math
import re
from datetime import datetime
from functools import lru_cache
from html import escape
from io import BytesIO
from pathlib import Path

import reportlab
from reportlab.graphics.shapes import Drawing, Line, Polygon, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, KeepInFrame, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from database import DATABASE_PATH
from interview_reports import DIMENSIONS, TOPIC_DIMENSIONS, get_report, overview_text, rounded_score, visible_findings


NAVY = colors.HexColor("#173649")
TEAL = colors.HexColor("#237d78")
MUTED = colors.HexColor("#5b6d79")
PALE = colors.HexColor("#eef4f5")


@lru_cache(maxsize=1)
def _fonts():
    folder = Path(reportlab.__file__).parent / "fonts"
    pdfmetrics.registerFont(TTFont("ReportBody", str(folder / "Vera.ttf")))
    pdfmetrics.registerFont(TTFont("ReportBold", str(folder / "VeraBd.ttf")))


def score_text(value):
    return "Not assessed" if value is None else f"{rounded_score(value)}/100"


def duration_text(source):
    if not source["ended_at"]:
        return "-"
    seconds = max(0, int((datetime.fromisoformat(source["ended_at"]) - datetime.fromisoformat(source["started_at"])).total_seconds()))
    return f"{seconds // 60}m {seconds % 60:02d}s"


def radar_svg(scores):
    cx, cy, radius = 250, 172, 101
    points = lambda scale: " ".join(f"{cx + radius * scale * math.cos(-math.pi/2 + i*2*math.pi/5):.1f},{cy + radius * scale * math.sin(-math.pi/2 + i*2*math.pi/5):.1f}" for i in range(5))
    shapes = [f'<polygon points="{points(level / 100)}" fill="none" stroke="#78909c" stroke-opacity=".25"/>' for level in (25,50,75,100)]
    for i in range(5):
        angle = -math.pi/2+i*2*math.pi/5
        shapes.append(f'<line x1="{cx}" y1="{cy}" x2="{cx+radius*math.cos(angle):.1f}" y2="{cy+radius*math.sin(angle):.1f}" stroke="#78909c" stroke-opacity=".25"/>')
    values = [rounded_score(scores[name]) for name in DIMENSIONS]
    if all(value is not None for value in values):
        polygon = " ".join(f"{cx+radius*value/100*math.cos(-math.pi/2+i*2*math.pi/5):.1f},{cy+radius*value/100*math.sin(-math.pi/2+i*2*math.pi/5):.1f}" for i,value in enumerate(values))
        shapes.append(f'<polygon points="{polygon}" fill="#369b99" fill-opacity=".18" stroke="#38aaa1" stroke-width="2.5"/>')
    positions = [(250,35),(421,147),(354,306),(145,306),(79,147)]
    for (name,label),(x,y) in zip(DIMENSIONS.items(),positions):
        value = "N/A" if scores[name] is None else str(rounded_score(scores[name]))
        short = "Correctness" if name == "technical_correctness" else label
        shapes.append(f'<text x="{x}" y="{y}" text-anchor="middle" fill="currentColor" font-size="12">{escape(short)}</text><text x="{x}" y="{y+18}" text-anchor="middle" fill="#38aaa1" font-size="13" font-weight="600">{value}</text>')
    return '<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Interview dimension scores" viewBox="0 0 500 350" style="width:100%;max-height:300px">'+"".join(shapes)+'</svg>'


def _radar(scores):
    drawing = Drawing(270,230)
    cx,cy,r=135,113,69
    for fraction in (.25,.5,.75,1):
        coords=[coordinate for i in range(5) for coordinate in (cx+r*fraction*math.cos(math.pi/2-i*2*math.pi/5),cy+r*fraction*math.sin(math.pi/2-i*2*math.pi/5))]
        drawing.add(Polygon(coords,fillColor=None,strokeColor=colors.HexColor('#d8e4e7'),strokeWidth=.6))
    for i in range(5):
        angle=math.pi/2-i*2*math.pi/5
        drawing.add(Line(cx,cy,cx+r*math.cos(angle),cy+r*math.sin(angle),strokeColor=colors.HexColor('#d8e4e7'),strokeWidth=.6))
    values=[rounded_score(scores[name]) for name in DIMENSIONS]
    if all(value is not None for value in values):
        coords=[coordinate for i,value in enumerate(values) for coordinate in (cx+r*value/100*math.cos(math.pi/2-i*2*math.pi/5),cy+r*value/100*math.sin(math.pi/2-i*2*math.pi/5))]
        drawing.add(Polygon(coords,fillColor=colors.HexColor('#d9eeee'),strokeColor=TEAL,strokeWidth=1.5))
    labels=[('Correctness',135,209),('Relevance',227,143),('Depth',202,19),('Clarity',67,19),('Role alignment',43,143)]
    for (name,_),(label,x,y) in zip(DIMENSIONS.items(),labels):
        value='N/A' if scores[name] is None else str(rounded_score(scores[name]))
        drawing.add(String(x,y,label,textAnchor='middle',fontName='ReportBody',fontSize=7.5,fillColor=MUTED))
        drawing.add(String(x,y-12,value,textAnchor='middle',fontName='ReportBold',fontSize=8,fillColor=TEAL))
    return drawing


def _compact(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 3].rsplit(" ", 1)[0] + "..."


def _question_assessment(turn, topic):
    if not topic or not turn["answer_text"]:
        return "No usable answer recorded; not assessed.", None
    dimensions = topic["dimensions"]
    scores = [dimensions[name]["score"] for name in TOPIC_DIMENSIONS if dimensions[name]["score"] is not None]
    score = sum(scores) / len(scores) if scores else None
    supported = [dimensions[name] for name in TOPIC_DIMENSIONS
                 if any(item["turn_id"] == turn["turn_id"] for item in dimensions[name]["evidence"])]
    if not supported:
        return "Assessed with the main answer and follow-ups; no separate finding recorded for this answer.", score
    substantive = [dimensions[name] for name in ("technical_correctness", "depth") if dimensions[name] in supported]
    supported = substantive or supported
    terms = set(re.findall(r"[a-z]{4,}", turn["question"].lower()))
    best = max(supported, key=lambda item: len(terms & set(re.findall(r"[a-z]{4,}", item["explanation"].lower()))))
    return best["explanation"], score


def export_report_pdf(report_id, database_path=DATABASE_PATH):
    """Two-page hiring summary; full transcripts remain in the web report."""
    _fonts()
    report = get_report(report_id, database_path=database_path)
    if not report or report["status"] != "ready":
        raise ValueError("This report is not ready to download.")
    source, result = report["source"], report["assessment"]
    width = A4[0] - 88
    styles = {
        "title": ParagraphStyle("title", fontName="ReportBold", fontSize=23, leading=29, textColor=NAVY, spaceAfter=9),
        "heading": ParagraphStyle("heading", fontName="ReportBold", fontSize=12, leading=17, textColor=NAVY, spaceBefore=14, spaceAfter=7),
        "body": ParagraphStyle("body", fontName="ReportBody", fontSize=9, leading=14, textColor=NAVY, spaceAfter=7),
        "small": ParagraphStyle("small", fontName="ReportBody", fontSize=8, leading=11, textColor=MUTED, spaceAfter=5),
        "cell": ParagraphStyle("cell", fontName="ReportBody", fontSize=8, leading=11, textColor=NAVY),
        "label": ParagraphStyle("label", fontName="ReportBold", fontSize=8, leading=11, textColor=TEAL),
        "score": ParagraphStyle("score", fontName="ReportBold", fontSize=27, leading=33, textColor=TEAL),
    }

    def p(text, style="body"):
        clean = str(text).replace("\x7f", "")
        for character in ("\u2010", "\u2011", "\u2013", "\u2014", "\u2212"):
            clean = clean.replace(character, "-")
        return Paragraph(escape(clean).replace("\n", "<br/>"), styles[style])

    def table(rows, widths, row_heights=None):
        grid = Table(rows, colWidths=widths, rowHeights=row_heights, hAlign="LEFT")
        grid.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PALE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7fafa")]),
            ("LINEBELOW", (0, 0), (-1, 0), .7, colors.HexColor("#d0e0e3")),
            ("LINEBELOW", (0, 1), (-1, -1), .3, colors.HexColor("#e5edef")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        return grid

    page_one = [p("TECHNICAL INTERVIEW ASSESSMENT", "label"), Spacer(1, 10),
                p(_compact(source["full_name"], 90), "title"), p(_compact(source["role"], 120)),
                Spacer(1, 5), HRFlowable(width="100%", thickness=1, color=TEAL), Spacer(1, 16)]
    answered = sum(bool(turn["answer_text"]) for turn in source["turns"])
    overview = Table([[
        [p("OVERALL INTERVIEW SCORE", "label"), p(score_text(result["overall_score"]), "score")],
        [p("ASSESSMENT COVERAGE", "label"), p(f"{result['coverage']}/6 topics assessed"),
         p(f"{answered} questions answered", "small")],
    ]], colWidths=[width / 2] * 2)
    overview.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    page_one.append(overview)
    score_rows = [[p("DIMENSION", "label"), p("SCORE", "label")]]
    score_rows += [[p(label, "cell"), p(score_text(result["dimension_scores"][name]), "label")]
                   for name, label in DIMENSIONS.items()]
    chart = Table([[_radar(result["dimension_scores"]), table(score_rows, [158, width - 428])]],
                  colWidths=[270, width - 270])
    chart.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    page_one.extend([Spacer(1, 8), chart, p("Summary", "heading"), p(_compact(overview_text(result), 480))])
    for heading, key in (("Demonstrated strengths", "strengths"), ("Areas to explore further", "gaps")):
        page_one.append(p(heading, "heading"))
        findings = visible_findings(result, key)[:2]
        if findings:
            page_one.extend(p("• " + _compact(item["text"], 185)) for item in findings)
        else:
            page_one.append(p("No additional finding within the assessed topics.", "small"))
    page_one.extend([Spacer(1, 9), p("Based on recorded answers only. Untested skills are not treated as weaknesses. Scores are rounded for display.", "small")])

    page_two = [p("QUESTION-BY-QUESTION ASSESSMENT", "label"), Spacer(1, 8),
                p("Interview responses", "title"),
                p("Main answers and their follow-ups are assessed together. The topic score is shared across those questions; role alignment is assessed across the interview.", "small"),
                Spacer(1, 10)]
    widths = [60, 180, width - 300, 60]
    rows = [[p(value, "label") for value in ("NO.", "QUESTION", "ASSESSMENT", "TOPIC /100")]]
    topics = {topic["main_number"]: topic for topic in result["topics"]}
    for turn in source["turns"]:
        feedback, score = _question_assessment(turn, topics.get(turn["main_number"]))
        values = [f"Q{turn['sequence']}\n" + ("Main" if turn["kind"] == "main" else "Follow-up"),
                  _compact(turn["question"], 150), _compact(feedback, 180),
                  "N/A" if score is None else str(rounded_score(score))]
        cells = [KeepInFrame(cell_width - 18, 45, [p(value, "cell")], mode="shrink")
                 for value, cell_width in zip(values, widths)]
        rows.append(cells)
    page_two.append(table(rows, widths, [30] + [62] * (len(rows) - 1)))
    page_two.extend([Spacer(1, 10), p("Question wording and findings are condensed. Full answers and supporting evidence are available in the detailed web report.", "small")])

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#dce7e8"))
        canvas.line(44, 38, A4[0] - 44, 38)
        canvas.setFont("ReportBody", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(44, 26, "Technical interview assessment")
        canvas.drawRightString(A4[0] - 44, 26, f"{doc.page} / 2")
        canvas.restoreState()

    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=44, rightMargin=44,
                                 topMargin=38, bottomMargin=52,
                                 title=f"Interview assessment - {source['full_name']}", author="Recruitment workspace")
    page_height = A4[1] - 102
    story = [KeepInFrame(width, page_height, page_one, mode="shrink"), PageBreak(),
             KeepInFrame(width, page_height, page_two, mode="shrink")]
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
