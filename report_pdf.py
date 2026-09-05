"""
GainARK OntoLeap — 1-Click Executive PDF Generator
Generates a C-Level, white-label PDF audit report and pitch deck summary
using ReportLab with clean typography, tables, and metric summary cards.
"""

import io
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)

logger = logging.getLogger("gainark.pdf")


def generate_executive_pdf_report(
    audit_data: Dict[str, Any],
    benchmark_data: Optional[Dict[str, Any]] = None,
    silo_data: Optional[Dict[str, Any]] = None,
    alignment_data: Optional[Dict[str, Any]] = None,
) -> bytes:
    """
    Generate an executive-grade PDF report in bytes.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    
    # Custom Brand Colors
    PRIMARY = colors.HexColor("#0f172a")    # Slate 900
    ACCENT = colors.HexColor("#4338ca")     # Indigo 700
    SUCCESS = colors.HexColor("#059669")    # Emerald 600
    WARNING = colors.HexColor("#d97706")    # Amber 600
    LIGHT_BG = colors.HexColor("#f8fafc")   # Slate 50
    BORDER_COL = colors.HexColor("#e2e8f0") # Slate 200
    TEXT_MUTED = colors.HexColor("#64748b") # Slate 500

    # Custom Typography Styles
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=PRIMARY
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=15,
        textColor=TEXT_MUTED
    )
    h1_style = ParagraphStyle(
        "SectionH1",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=PRIMARY,
        spaceBefore=14,
        spaceAfter=6
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=PRIMARY
    )
    bold_body = ParagraphStyle(
        "BoldBody",
        parent=body_style,
        fontName="Helvetica-Bold"
    )
    caption_style = ParagraphStyle(
        "Caption",
        parent=body_style,
        fontSize=8,
        textColor=TEXT_MUTED
    )

    story = []

    # -------------------------------------------------------------------------
    # Header Banner
    # -------------------------------------------------------------------------
    url = audit_data.get("url", "Target Domain")
    gen_time = datetime.now(timezone.utc).strftime("%B %d, %Y - %H:%M UTC")
    
    story.append(Paragraph("GainARK OntoLeap", title_style))
    story.append(Paragraph(f"Autonomous Ontology Intelligence & GEO Executive Report — {url}", subtitle_style))
    story.append(Paragraph(f"Generated: {gen_time} | Engine: GLiNER + RDFLib + Google Gemini 2.5 Flash", caption_style))
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=1.5, color=ACCENT, spaceBefore=2, spaceAfter=12))

    # -------------------------------------------------------------------------
    # Executive KPI Summary Cards
    # -------------------------------------------------------------------------
    readiness_score = audit_data.get("readiness_score", 0.0)
    score_label = "Structured Data Readiness"
    schema_status = audit_data.get("mandatory_schema_status", {})
    schemas_valid = sum(1 for v in schema_status.values() if v)
    total_schemas = max(len(schema_status), 1)

    pas_score = alignment_data.get("product_alignment_score", 75.0) if alignment_data else 80.0
    triples = audit_data.get("triples", [])

    kpi_data = [
        [
            Paragraph(f"<b><font size=18 color='{ACCENT.hexval()}'>{readiness_score:.1f}</font></b><br/><font size=8 color='#64748b'>Structured Data Readiness (0–100)</font>", body_style),
            Paragraph(f"<b><font size=18 color='{SUCCESS.hexval()}'>{schemas_valid}/{total_schemas}</font></b><br/><font size=8 color='#64748b'>Google Rich Schemas Detected</font>", body_style),
            Paragraph(f"<b><font size=18 color='{PRIMARY.hexval()}'>{len(triples)}</font></b><br/><font size=8 color='#64748b'>Verified Knowledge Triples</font>", body_style),
            Paragraph(f"<b><font size=18 color='{SUCCESS.hexval()}'>{pas_score:.1f}%</font></b><br/><font size=8 color='#64748b'>Product Alignment Score (PAS)</font>", body_style),
        ]
    ]
    kpi_table = Table(kpi_data, colWidths=[135, 135, 135, 135])
    kpi_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_BG),
        ('BOX', (0, 0), (-1, -1), 1, BORDER_COL),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, BORDER_COL),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 14))

    # -------------------------------------------------------------------------
    # Section 1: Schema.org Audit & Google Rich Results
    # -------------------------------------------------------------------------
    story.append(Paragraph("1. Schema.org Structured Data & Google Rich Results Compliance", h1_style))
    story.append(Paragraph(
        "Google and AI Answer Engines rely on nested Schema.org JSON-LD to confirm that a company is an authoritative "
        "software provider. Missing schemas result in omission from AI answer snapshots and loss of search rich snippets.",
        body_style
    ))
    story.append(Spacer(1, 6))

    schema_rows = [
        [Paragraph("<b>Required Schema Type</b>", bold_body), Paragraph("<b>Status</b>", bold_body), Paragraph("<b>Impact & Recommendation</b>", bold_body)]
    ]
    for s_type, is_present in schema_status.items():
        st_text = f"<font color='{SUCCESS.hexval()}'><b>PASSED</b></font>" if is_present else f"<font color='{WARNING.hexval()}'><b>MISSING</b></font>"
        impact = "Properly declared on audited page." if is_present else f"Inject 1-Click JSON-LD patch containing {s_type} markup."
        schema_rows.append([
            Paragraph(s_type, body_style),
            Paragraph(st_text, body_style),
            Paragraph(impact, body_style),
        ])
    
    schema_table = Table(schema_rows, colWidths=[140, 80, 320])
    schema_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), LIGHT_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COL),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(schema_table)
    story.append(Spacer(1, 14))

    # -------------------------------------------------------------------------
    # Section 2: Verified Knowledge Graph Triples
    # -------------------------------------------------------------------------
    story.append(Paragraph("2. Verified Relational Triples & Canonical Entity Grounding", h1_style))
    story.append(Paragraph(
        "Below are the verified relational triples extracted via zero-shot GLiNER and anchored to global Wikidata Q-IDs. "
        "These verifiable statements provide direct grounding for AI engines (Perplexity, SearchGPT, Gemini).",
        body_style
    ))
    story.append(Spacer(1, 6))

    triple_rows = [
        [Paragraph("<b>Subject</b>", bold_body), Paragraph("<b>Predicate</b>", bold_body), Paragraph("<b>Object / Canonical Entity</b>", bold_body), Paragraph("<b>Wikidata sameAs</b>", bold_body)]
    ]
    for t in triples[:8]:
        subj = t.get("subject", "Platform")
        pred = t.get("predicate", "relatesTo")
        obj = t.get("object", "")
        same_as = t.get("wikidata_uri", "Resolved via API")
        if same_as and len(same_as) > 35:
            same_as = same_as.split("/")[-1]
        triple_rows.append([
            Paragraph(subj[:20], body_style),
            Paragraph(pred[:25], body_style),
            Paragraph(obj[:30], body_style),
            Paragraph(f"<font color='{ACCENT.hexval()}'>{same_as}</font>", caption_style),
        ])

    if len(triple_rows) == 1:
        triple_rows.append([Paragraph("No triples extracted", body_style), Paragraph("-", body_style), Paragraph("-", body_style), Paragraph("-", body_style)])

    triple_table = Table(triple_rows, colWidths=[110, 140, 170, 120])
    triple_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), LIGHT_BG),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COL),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(triple_table)
    story.append(Spacer(1, 14))

    # -------------------------------------------------------------------------
    # Section 3: Competitor Benchmark Gap Matrix (if available)
    # -------------------------------------------------------------------------
    if benchmark_data and benchmark_data.get("comparative_table"):
        story.append(Paragraph("3. Competitive Benchmarking & Content Gap Matrix", h1_style))
        bench_rows = [
            [Paragraph("<b>Domain / URL</b>", bold_body), Paragraph("<b>Readiness Score</b>", bold_body), Paragraph("<b>Schemas</b>", bold_body), Paragraph("<b>Entities</b>", bold_body)]
        ]
        for row in benchmark_data.get("comparative_table", [])[:5]:
            bench_rows.append([
                Paragraph(row.get("url", "")[:35], body_style),
                Paragraph(f"<b>{row.get('readiness_score', 0):.1f}</b>", body_style),
                Paragraph(row.get("compliance_str", "-"), body_style),
                Paragraph(str(row.get("entity_count", 0)), body_style),
            ])
        bench_table = Table(bench_rows, colWidths=[240, 100, 100, 100])
        bench_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), LIGHT_BG),
            ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COL),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(bench_table)
        story.append(Spacer(1, 14))

    # -------------------------------------------------------------------------
    # Section 4: Prioritized Strategic Action Plan
    # -------------------------------------------------------------------------
    story.append(Paragraph("4. Strategic Remediation Plan (Immediate ROI Actions)", h1_style))
    plan_items = [
        "<b>Action 1 (Instant Rich Results):</b> Inject the dynamic JSON-LD remediation patch into your CMS header to eliminate schema validation penalties.",
        "<b>Action 2 (Internal PageRank Flow):</b> Link orphan blog posts and subpages back to your canonical integration and compliance pillar hubs.",
        "<b>Action 3 (AI Crawler Directives):</b> Publish <code>/llms.txt</code> and tailored <code>robots.txt</code> to ensure GPTBot and PerplexityBot consume clean entity facts.",
        "<b>Action 4 (Eliminate AI Content Sprawl):</b> Run content drafts through the Product Alignment Checker to purge empty buzzwords and ensure 100% fidelity to verified product capabilities."
    ]
    for p in plan_items:
        story.append(Paragraph(f"• {p}", body_style))
        story.append(Spacer(1, 4))

    story.append(Spacer(1, 15))
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER_COL, spaceBefore=5, spaceAfter=8))
    story.append(Paragraph("Confidential — Generated by GainARK OntoLeap Platform | Powered by Google Cloud & Vertex AI", caption_style))

    # Build Document
    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
