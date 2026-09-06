"""
Generate a professional 1-Page Executive Pitch PDF for OntoLeap.
Strictly calibrated to fit on exactly 1 Letter-size page.
"""

import os
import io
from typing import Any
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
    """Ensures exact single-page canvas handling."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            # Add subtle footer
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor("#64748b"))
            self.drawString(36, 18, "CONFIDENTIAL — OntoLeap Executive Briefing")
            self.drawRightString(576, 18, "ontoleap.ai  |  contact@gainark.com")
            super().showPage()
        super().save()


def build_one_pager_pdf(output_target: Any = "OntoLeap_Executive_Pitch_OnePager.pdf"):
    # Letter is 612 x 792 pt. Margins: 32 pt left/right, 24 pt top/bottom.
    # Printable width: 548 pt, printable height: 744 pt.
    doc = SimpleDocTemplate(
        output_target,
        pagesize=letter,
        leftMargin=32,
        rightMargin=32,
        topMargin=24,
        bottomMargin=24
    )

    styles = getSampleStyleSheet()

    # Brand Colors
    PRIMARY = colors.HexColor("#0f172a")     # Deep Slate 900
    ACCENT = colors.HexColor("#4338ca")      # Royal Indigo 700
    TEXT_MAIN = colors.HexColor("#1e293b")   # Slate 800
    TEXT_MUTED = colors.HexColor("#64748b")  # Slate 500
    BORDER_COL = colors.HexColor("#cbd5e1")  # Slate 300

    # Typography Styles
    brand_title = ParagraphStyle(
        "BrandTitle",
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=22,
        textColor=PRIMARY
    )
    brand_tagline = ParagraphStyle(
        "BrandTagline",
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=10.5,
        textColor=ACCENT
    )
    badge_style = ParagraphStyle(
        "Badge",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#4338ca"),
        alignment=2 # Right aligned
    )
    body_text = ParagraphStyle(
        "BodyTextCustom",
        fontName="Helvetica",
        fontSize=7.8,
        leading=10,
        textColor=TEXT_MAIN
    )
    body_bold = ParagraphStyle(
        "BodyBoldCustom",
        fontName="Helvetica-Bold",
        fontSize=7.8,
        leading=10,
        textColor=TEXT_MAIN
    )
    section_heading = ParagraphStyle(
        "SecHead",
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=11.5,
        textColor=PRIMARY
    )
    metric_num = ParagraphStyle(
        "MetricNum",
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=15,
        textColor=ACCENT,
        alignment=1
    )
    metric_label = ParagraphStyle(
        "MetricLabel",
        fontName="Helvetica",
        fontSize=6.8,
        leading=8.5,
        textColor=TEXT_MUTED,
        alignment=1
    )

    story = []

    # 1. HEADER / MASTHEAD
    header_data = [
        [
            Paragraph("<b>OntoLeap</b> <font size=8 color='#64748b'>by GainARK</font>", brand_title),
            Paragraph("EXECUTIVE PRODUCT BRIEFING<br/><font color='#64748b' size=7>Generative Engine Optimization (GEO) & Truth Platform</font>", badge_style)
        ],
        [
            Paragraph("THE FIRST TRUTH-VERIFIED COMPETITIVE INTELLIGENCE & AI CITATION ENGINE", brand_tagline),
            Paragraph("<font color='#059669'><b>Status: Production Ready (v2.2)</b></font>", badge_style)
        ]
    ]
    header_table = Table(header_data, colWidths=[360, 188])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('TOPPADDING', (0,0), (-1,-1), 1),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=1.5, color=ACCENT, spaceBefore=3, spaceAfter=6))

    # 2. THE BIG SHIFT & THE CORE PROBLEM (2-Column Box)
    problem_text = """
    <b>1. The Generative Search Shift:</b> B2B buyers no longer browse 10 blue links on Google. They ask <b>ChatGPT, Perplexity, and Claude</b> who to buy. If your product is not mapped into machine-readable Knowledge Graphs (JSON-LD), AI search engines hallucinate or recommend your competitor.<br/><br/>
    <b>2. The Product Truth Gap:</b> Marketing writes aspirational claims on websites, while engineering builds APIs. When claims fail in technical bake-offs, deals are lost and churn spikes.
    """
    solution_text = """
    <b>OntoLeap Bridges the Gap:</b> In 60 seconds, OntoLeap crawls your website, extracts your entities, verifies your marketing claims against OpenAPI/code reality, and outputs ready-to-use executive sales battlecards.<br/><br/>
    <b>Outcome:</b> Guarantees your brand is <b>cited as #1 in AI search engines</b> and equips sales reps with <b>mathematically verified counter-arguments</b> against competitors.
    """
    problem_solution_data = [
        [
            Paragraph("<font color='#b91c1c'><b>THE MARKET CRISIS</b></font>", section_heading),
            Paragraph("<font color='#4338ca'><b>THE ONTOLEAP SOLUTION</b></font>", section_heading)
        ],
        [
            Paragraph(problem_text, body_text),
            Paragraph(solution_text, body_text)
        ]
    ]
    ps_table = Table(problem_solution_data, colWidths=[270, 270])
    ps_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,-1), colors.HexColor("#fff1f2")), # Subtle Red
        ('BACKGROUND', (1,0), (1,-1), colors.HexColor("#f5f3ff")), # Subtle Indigo
        ('BOX', (0,0), (0,-1), 1, colors.HexColor("#fecdd3")),
        ('BOX', (1,0), (1,-1), 1, colors.HexColor("#ddd6fe")),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 7),
        ('RIGHTPADDING', (0,0), (-1,-1), 7),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(ps_table)
    story.append(Spacer(1, 6))

    # 3. HOW IT WORKS: THE 3 ENGINES
    story.append(Paragraph("<b>CORE PLATFORM CAPABILITIES</b>", section_heading))
    story.append(Spacer(1, 3))

    arch_data = [
        [
            Paragraph("<b>1. Tri-Ontology Mapper</b>", body_bold),
            Paragraph("<b>2. Product Truth Engine</b>", body_bold),
            Paragraph("<b>3. AI Citation Authority</b>", body_bold)
        ],
        [
            Paragraph("Extracts marketing concepts via zero-shot GLiNER NER and connects them to industry schema taxonomies.", body_text),
            Paragraph("Executes automated assertion checks comparing marketing claims directly against real OpenAPI/Swagger specifications.", body_text),
            Paragraph("Scores site-wide citation authority across 4 pillars (25% each): Entity Grounding, Relational Density, Topic Silo Integrity, and Schema Coverage.", body_text)
        ],
        [
            Paragraph("<font color='#4338ca'><b>Output:</b> Unified Knowledge Graph</font>", body_text),
            Paragraph("<font color='#15803d'><b>Output:</b> Proven vs Disproven Claims</font>", body_text),
            Paragraph("<font color='#059669'><b>Output:</b> Executive Battlecard PDF</font>", body_text)
        ]
    ]
    arch_table = Table(arch_data, colWidths=[180, 180, 180])
    arch_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f8fafc")),
        ('BOX', (0,0), (-1,-1), 1, BORDER_COL),
        ('INNERGRID', (0,0), (-1,-1), 0.5, BORDER_COL),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(arch_table)
    story.append(Spacer(1, 6))

    # 4. COMPETITIVE LANDSCAPE (HOW WE COMPARE)
    story.append(Paragraph("<b>WHY ONTOLEAP WINS: COMPETITIVE COMPARISON</b>", section_heading))
    story.append(Spacer(1, 3))

    comp_data = [
        [
            Paragraph("<b>Capability</b>", body_bold),
            Paragraph("<b>Legacy SEO (Semrush/Ahrefs)</b>", body_bold),
            Paragraph("<b>CI Scraping (Klue/Crayon)</b>", body_bold),
            Paragraph("<b>OntoLeap Platform</b>", body_bold)
        ],
        [
            Paragraph("<b>Target Search Medium</b>", body_text),
            Paragraph("Legacy Google 10 Blue Links", body_text),
            Paragraph("Manual Internal Dashboards", body_text),
            Paragraph("<font color='#4338ca'><b>ChatGPT, Perplexity & Claude</b></font>", body_bold)
        ],
        [
            Paragraph("<b>Truth Verification</b>", body_text),
            Paragraph("<font color='#b91c1c'>None</font> (Only keyword volume)", body_text),
            Paragraph("<font color='#b91c1c'>None</font> (Only text diffs)", body_text),
            Paragraph("<font color='#15803d'><b>Automated API Assertions</b></font>", body_bold)
        ],
        [
            Paragraph("<b>Battlecard Creation</b>", body_text),
            Paragraph("Not supported", body_text),
            Paragraph("Manual curation ($35k+/yr)", body_text),
            Paragraph("<font color='#15803d'><b>1-Click Autonomous Generation</b></font>", body_bold)
        ],
        [
            Paragraph("<b>AI Citation Index (GEO)</b>", body_text),
            Paragraph("No LLM awareness", body_text),
            Paragraph("No LLM awareness", body_text),
            Paragraph("<font color='#4338ca'><b>4-Pillar Site-Wide Index</b></font>", body_bold)
        ]
    ]
    comp_table = Table(comp_data, colWidths=[120, 140, 140, 140])
    comp_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), PRIMARY),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('BACKGROUND', (3,1), (3,-1), colors.HexColor("#eef2ff")), # Highlight OntoLeap
        ('BOX', (0,0), (-1,-1), 1, BORDER_COL),
        ('INNERGRID', (0,0), (-1,-1), 0.5, BORDER_COL),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING', (0,0), (-1,-1), 5),
        ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(comp_table)
    story.append(Spacer(1, 6))

    # 5. BUSINESS IMPACT & BUYER PERSONAS
    story.append(Paragraph("<b>COMMERCIAL IMPACT & TARGET METRICS</b>", section_heading))
    story.append(Spacer(1, 3))

    impact_data = [
        [
            Paragraph("<b>+40%</b>", metric_num),
            Paragraph("<b>60 Sec</b>", metric_num),
            Paragraph("<b>#1</b>", metric_num),
            Paragraph("<b>100%</b>", metric_num)
        ],
        [
            Paragraph("Competitive Win Rate in Tech Bake-Offs", metric_label),
            Paragraph("Time to Generate Executive Battlecards", metric_label),
            Paragraph("Citation Position in Perplexity & SearchGPT", metric_label),
            Paragraph("Verified Claims Backed by API Proof", metric_label)
        ]
    ]
    impact_table = Table(impact_data, colWidths=[135, 135, 135, 135])
    impact_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f8fafc")),
        ('BOX', (0,0), (-1,-1), 1, BORDER_COL),
        ('INNERGRID', (0,0), (-1,-1), 0.5, BORDER_COL),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ]))
    story.append(impact_table)
    story.append(Spacer(1, 6))

    # 6. CALL TO ACTION / FOOTER BANNER
    cta_data = [
        [
            Paragraph("<b>WHO BUYS ONTOLEAP?</b><br/><font size=7 color='#64748b'>• VP Marketing & PMMs: Instant, real-time sales battlecards<br/>• SEO & Growth Leaders: Generative Engine Optimization (GEO)<br/>• Product & DevRel: Eliminates marketing-API drift and customer churn</font>", body_text),
            Paragraph("<b>RUN A LIVE 60-SECOND AUDIT TODAY</b><br/><font color='#4338ca'><b>https://gainark-ontoleap-35509275124.asia-south1.run.app</b></font><br/><font size=7 color='#64748b'>Live Cloud Run Microservice (API v2.2.0) — Zero Setup Required</font>", body_text)
        ]
    ]
    cta_table = Table(cta_data, colWidths=[270, 270])
    cta_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f1f5f9")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#94a3b8")),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(cta_table)

    # Build Document
    doc.build(story, canvasmaker=NumberedCanvas)
    if isinstance(output_target, str):
        print(f"Successfully generated 1-Page PDF at: {output_target}")


def build_one_pager_pdf_bytes() -> bytes:
    """Builds the 1-Page Executive Pitch PDF strictly in memory and returns raw bytes."""
    buf = io.BytesIO()
    build_one_pager_pdf(output_target=buf)
    return buf.getvalue()


if __name__ == "__main__":
    build_one_pager_pdf()
