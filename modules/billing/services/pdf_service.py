"""
InvoicePDFService — Professional PDF invoice generation using ReportLab.

Generates branded, auditable PDF invoices that include:
  - Company header with branding
  - Invoice metadata (number, date, tenant, status)
  - Line items table with alternating row colours
  - Contract & usage summary
  - Financial totals block
  - Notes / payment terms footer

Design decisions:
  - ReportLab chosen over WeasyPrint because it is already in the Pipfile,
    needs zero system-level dependencies, and produces pixel-perfect output.
  - All amounts are formatted as strings before rendering to avoid
    float precision artefacts on the PDF surface.
  - Colour palette: navy (#1B2A4A), accent blue (#3B82F6), light grey (#F3F4F6).
"""

import io
import calendar
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm, cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable,
)

logger = logging.getLogger(__name__)

# ============================================================
# COLOUR PALETTE
# ============================================================
NAVY       = colors.HexColor("#1B2A4A")
ACCENT     = colors.HexColor("#3B82F6")
DARK_GREY  = colors.HexColor("#374151")
MID_GREY   = colors.HexColor("#6B7280")
LIGHT_GREY = colors.HexColor("#F3F4F6")
WHITE      = colors.white
GREEN      = colors.HexColor("#10B981")
RED        = colors.HexColor("#EF4444")
AMBER      = colors.HexColor("#F59E0B")

# ============================================================
# COMPANY BRANDING — loaded from settings (configurable via .env)
# ============================================================
from core.settings import settings as _settings

def _company_name():    return _settings.company_name
def _company_tagline(): return _settings.company_tagline
def _company_address(): return _settings.company_address
def _company_email():   return _settings.company_email
def _company_web():     return _settings.company_web


class InvoicePDFService:
    """Generates professional PDF invoices from billing data."""

    def __init__(self) -> None:
        self.styles = getSampleStyleSheet()
        self._register_custom_styles()

    # ================================================================
    # PUBLIC API
    # ================================================================

    def generate(self, invoice_data: dict) -> bytes:
        """
        Generate a complete PDF invoice from invoice data.

        Args:
            invoice_data: dict with keys 'invoice', 'line_items', 'report'
                          (same shape as BillingService.get_invoice() output)

        Returns:
            PDF file content as bytes.
        """
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=15 * mm,
            bottomMargin=20 * mm,
            title=self._invoice_title(invoice_data),
            author=_company_name(),
        )

        elements = []
        inv = invoice_data["invoice"]
        line_items = invoice_data.get("line_items", [])
        report = invoice_data.get("report")

        # ── 1. Header ─────────────────────────────────────────────
        elements.extend(self._build_header(inv))
        elements.append(Spacer(1, 8 * mm))

        # ── 2. Invoice metadata ───────────────────────────────────
        elements.extend(self._build_metadata_block(inv))
        elements.append(Spacer(1, 6 * mm))

        # ── 3. Line items table ───────────────────────────────────
        elements.extend(self._build_line_items_table(line_items))
        elements.append(Spacer(1, 6 * mm))

        # ── 4. Totals block ───────────────────────────────────────
        elements.extend(self._build_totals_block(inv))
        elements.append(Spacer(1, 6 * mm))

        # ── 5. Contract & usage summary ───────────────────────────
        elements.extend(self._build_contract_summary(inv))
        elements.append(Spacer(1, 6 * mm))

        # ── 6. Executive summary (from report) ────────────────────
        if report and report.get("executive_summary"):
            elements.extend(self._build_report_section(report))
            elements.append(Spacer(1, 6 * mm))

        # ── 7. Footer / payment terms ─────────────────────────────
        elements.extend(self._build_footer(inv))

        doc.build(elements)
        pdf_bytes = buffer.getvalue()
        buffer.close()

        logger.info(
            "pdf.generated",
            tenant_id=inv.get("tenant_id"),
            year_month=inv.get("year_month"),
            size_bytes=len(pdf_bytes),
        )
        return pdf_bytes

    # ================================================================
    # SECTION BUILDERS
    # ================================================================

    def _build_header(self, inv: dict) -> list:
        """Company branding header with invoice title."""
        elements = []

        # Company name row
        header_data = [
            [
                Paragraph(_company_name(), self.styles["CompanyName"]),
                Paragraph("INVOICE", self.styles["InvoiceTitle"]),
            ],
            [
                Paragraph(_company_tagline(), self.styles["CompanyTagline"]),
                Paragraph(
                    f"#{self._invoice_number(inv)}",
                    self.styles["InvoiceNumber"],
                ),
            ],
        ]

        header_table = Table(
            header_data,
            colWidths=[100 * mm, 70 * mm],
        )
        header_table.setStyle(TableStyle([
            ("VALIGN",    (0, 0), (-1, -1), "TOP"),
            ("ALIGN",     (1, 0), (1, -1), "RIGHT"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ]))
        elements.append(header_table)

        # Divider line
        elements.append(Spacer(1, 3 * mm))
        elements.append(HRFlowable(
            width="100%", thickness=2, color=NAVY, spaceAfter=2 * mm,
        ))

        return elements

    def _build_metadata_block(self, inv: dict) -> list:
        """Two-column block: billing-to and invoice details."""
        elements = []
        month_name = self._month_name(inv.get("year_month", 0))
        status = inv.get("status", "draft").upper()

        # Status colour
        status_color = GREEN if status == "FINALIZED" else AMBER

        left_content = (
            f"<b>Billed To:</b><br/>"
            f"<b>{inv.get('tenant_id', 'N/A')}</b><br/>"
            f"Billing Period: {month_name}<br/>"
        )
        finalized_at = inv.get("finalized_at")
        issue_date = finalized_at if finalized_at else datetime.now(timezone.utc).strftime("%Y-%m-%d")

        right_content = (
            f"<b>Invoice Date:</b> {issue_date}<br/>"
            f"<b>Invoice No:</b> {self._invoice_number(inv)}<br/>"
            f"<b>Status:</b> <font color='{status_color}'>{status}</font><br/>"
            f"<b>Currency:</b> USD"
        )

        meta_data = [[
            Paragraph(left_content, self.styles["MetaText"]),
            Paragraph(right_content, self.styles["MetaText"]),
        ]]

        meta_table = Table(meta_data, colWidths=[90 * mm, 80 * mm])
        meta_table.setStyle(TableStyle([
            ("VALIGN",     (0, 0), (-1, -1), "TOP"),
            ("ALIGN",      (1, 0), (1, -1), "RIGHT"),
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREY),
            ("ROUNDEDCORNERS", [4, 4, 4, 4]),
            ("LEFTPADDING",  (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING",   (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 8),
        ]))
        elements.append(meta_table)
        return elements

    def _build_line_items_table(self, line_items: list) -> list:
        """Professional line items table with alternating row colours."""
        elements = []
        elements.append(Paragraph("Line Items", self.styles["SectionHeading"]))
        elements.append(Spacer(1, 3 * mm))

        # Header row
        header = [
            Paragraph("<b>Description</b>", self.styles["TableHeader"]),
            Paragraph("<b>Type</b>", self.styles["TableHeader"]),
            Paragraph("<b>Tokens</b>", self.styles["TableHeaderRight"]),
            Paragraph("<b>Calls</b>", self.styles["TableHeaderRight"]),
            Paragraph("<b>Amount (USD)</b>", self.styles["TableHeaderRight"]),
        ]

        rows = [header]
        for li in line_items:
            amount = Decimal(str(li.get("amount_usd", "0")))
            amount_str = f"${amount:,.4f}"
            if amount < 0:
                amount_str = f"<font color='#EF4444'>{amount_str}</font>"

            tokens = int(li.get("quantity_tokens", 0))
            calls  = int(li.get("quantity_calls", 0))

            row = [
                Paragraph(li.get("description", ""), self.styles["TableCell"]),
                Paragraph(
                    self._format_line_type(li.get("line_type", "")),
                    self.styles["TableCellCenter"],
                ),
                Paragraph(f"{tokens:,}" if tokens else "—", self.styles["TableCellRight"]),
                Paragraph(f"{calls:,}" if calls else "—", self.styles["TableCellRight"]),
                Paragraph(amount_str, self.styles["TableCellRight"]),
            ]
            rows.append(row)

        col_widths = [72 * mm, 22 * mm, 22 * mm, 18 * mm, 30 * mm]
        table = Table(rows, colWidths=col_widths, repeatRows=1)

        # Styling
        style_commands = [
            # Header
            ("BACKGROUND",    (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR",     (0, 0), (-1, 0), WHITE),
            ("FONTSIZE",      (0, 0), (-1, 0), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("TOPPADDING",    (0, 0), (-1, 0), 6),
            # Grid
            ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
            ("TOPPADDING",    (0, 1), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ]

        # Alternating row colours
        for i in range(1, len(rows)):
            if i % 2 == 0:
                style_commands.append(
                    ("BACKGROUND", (0, i), (-1, i), LIGHT_GREY)
                )

        table.setStyle(TableStyle(style_commands))
        elements.append(table)
        return elements

    def _build_totals_block(self, inv: dict) -> list:
        """Financial totals — right-aligned summary block."""
        elements = []

        raw_cost   = inv.get("raw_cost_usd", "0")
        base_fee   = inv.get("base_fee_usd", "0")
        overage    = inv.get("overage_charge_usd", "0")
        credits    = inv.get("credits_usd", "0")
        total      = inv.get("total_billed_usd", "0")

        totals_data = [
            ["Raw API Cost:", f"${Decimal(raw_cost):,.4f}"],
            ["Base Platform Fee:", f"${Decimal(base_fee):,.4f}"],
            ["Overage Charges:", f"${Decimal(overage):,.4f}"],
        ]

        credits_dec = Decimal(credits)
        if credits_dec > 0:
            totals_data.append(
                ["Credits / Adjustments:", f"-${credits_dec:,.4f}"]
            )

        # Separator
        totals_data.append(["", ""])

        # Grand total
        totals_data.append(["TOTAL DUE:", f"${Decimal(total):,.4f}"])

        rows = []
        for label, value in totals_data:
            if label == "TOTAL DUE:":
                rows.append([
                    Paragraph(f"<b>{label}</b>", self.styles["TotalLabel"]),
                    Paragraph(f"<b>{value}</b>", self.styles["TotalValue"]),
                ])
            elif "Credits" in label:
                rows.append([
                    Paragraph(label, self.styles["TotalLabelSmall"]),
                    Paragraph(
                        f"<font color='#EF4444'>{value}</font>",
                        self.styles["TotalValueSmall"],
                    ),
                ])
            else:
                rows.append([
                    Paragraph(label, self.styles["TotalLabelSmall"]),
                    Paragraph(value, self.styles["TotalValueSmall"]),
                ])

        table = Table(rows, colWidths=[40 * mm, 35 * mm], hAlign="RIGHT")
        style_commands = [
            ("ALIGN",      (0, 0), (0, -1), "RIGHT"),
            ("ALIGN",      (1, 0), (1, -1), "RIGHT"),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]

        # Total row styling
        total_row = len(rows) - 1
        style_commands.extend([
            ("BACKGROUND",    (0, total_row), (-1, total_row), NAVY),
            ("TEXTCOLOR",     (0, total_row), (-1, total_row), WHITE),
            ("TOPPADDING",    (0, total_row), (-1, total_row), 6),
            ("BOTTOMPADDING", (0, total_row), (-1, total_row), 6),
        ])

        # Separator row (the empty one before total)
        sep_row = total_row - 1
        style_commands.append(
            ("LINEABOVE", (0, total_row), (-1, total_row), 1, NAVY)
        )

        table.setStyle(TableStyle(style_commands))
        elements.append(table)
        return elements

    def _build_contract_summary(self, inv: dict) -> list:
        """Contract usage summary — forfait details if applicable."""
        elements = []
        forfait_included = inv.get("forfait_tokens_included", 0)

        if forfait_included <= 0:
            # PAYG — no contract summary needed
            return elements

        elements.append(Paragraph("Contract Summary", self.styles["SectionHeading"]))
        elements.append(Spacer(1, 2 * mm))

        forfait_used   = inv.get("forfait_tokens_used", 0)
        overage_tokens = inv.get("overage_tokens", 0)
        pct_used = (forfait_used / forfait_included * 100) if forfait_included > 0 else 0

        summary_data = [
            ["Included Tokens:", f"{forfait_included:,}"],
            ["Tokens Used (within forfait):", f"{forfait_used:,}"],
            ["Forfait Utilisation:", f"{pct_used:.1f}%"],
            ["Overage Tokens:", f"{overage_tokens:,}"],
        ]

        rows = []
        for label, value in summary_data:
            rows.append([
                Paragraph(label, self.styles["MetaText"]),
                Paragraph(f"<b>{value}</b>", self.styles["MetaText"]),
            ])

        table = Table(rows, colWidths=[60 * mm, 50 * mm])
        table.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), LIGHT_GREY),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ]))
        elements.append(table)
        return elements

    def _build_report_section(self, report: dict) -> list:
        """Executive summary from the client report."""
        elements = []
        elements.append(Paragraph("Executive Summary", self.styles["SectionHeading"]))
        elements.append(Spacer(1, 2 * mm))
        elements.append(Paragraph(
            report["executive_summary"],
            self.styles["ExecutiveSummaryText"],
        ))
        return elements

    def _build_footer(self, inv: dict) -> list:
        """Payment terms and company contact info."""
        elements = []
        elements.append(HRFlowable(
            width="100%", thickness=1, color=MID_GREY, spaceBefore=4 * mm,
        ))
        elements.append(Spacer(1, 3 * mm))

        notes = inv.get("notes")
        if notes:
            elements.append(Paragraph(
                f"<b>Notes:</b> {notes}", self.styles["FooterText"],
            ))
            elements.append(Spacer(1, 2 * mm))

        elements.append(Paragraph(
            "<b>Payment Terms:</b> Net 30 days from invoice date. "
            "Please reference the invoice number in your payment.",
            self.styles["FooterText"],
        ))
        elements.append(Spacer(1, 4 * mm))
        elements.append(Paragraph(
            f"{_company_name()} · {_company_address()} · "
            f"{_company_email()} · {_company_web()}",
            self.styles["FooterContact"],
        ))
        elements.append(Spacer(1, 2 * mm))
        elements.append(Paragraph(
            f"Generated on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            self.styles["FooterTimestamp"],
        ))
        return elements

    # ================================================================
    # HELPERS
    # ================================================================

    def _register_custom_styles(self) -> None:
        """Register all custom paragraph styles."""
        self.styles.add(ParagraphStyle(
            name="CompanyName",
            fontName="Helvetica-Bold",
            fontSize=18,
            textColor=NAVY,
            leading=22,
        ))
        self.styles.add(ParagraphStyle(
            name="CompanyTagline",
            fontName="Helvetica",
            fontSize=9,
            textColor=MID_GREY,
            leading=12,
        ))
        self.styles.add(ParagraphStyle(
            name="InvoiceTitle",
            fontName="Helvetica-Bold",
            fontSize=24,
            textColor=ACCENT,
            alignment=TA_RIGHT,
            leading=28,
        ))
        self.styles.add(ParagraphStyle(
            name="InvoiceNumber",
            fontName="Helvetica",
            fontSize=10,
            textColor=DARK_GREY,
            alignment=TA_RIGHT,
            leading=14,
        ))
        self.styles.add(ParagraphStyle(
            name="SectionHeading",
            fontName="Helvetica-Bold",
            fontSize=12,
            textColor=NAVY,
            leading=16,
            spaceBefore=4,
        ))
        self.styles.add(ParagraphStyle(
            name="MetaText",
            fontName="Helvetica",
            fontSize=9,
            textColor=DARK_GREY,
            leading=13,
        ))
        self.styles.add(ParagraphStyle(
            name="TableHeader",
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=WHITE,
            leading=10,
        ))
        self.styles.add(ParagraphStyle(
            name="TableHeaderRight",
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=WHITE,
            alignment=TA_RIGHT,
            leading=10,
        ))
        self.styles.add(ParagraphStyle(
            name="TableCell",
            fontName="Helvetica",
            fontSize=7.5,
            textColor=DARK_GREY,
            leading=10,
        ))
        self.styles.add(ParagraphStyle(
            name="TableCellCenter",
            fontName="Helvetica",
            fontSize=7.5,
            textColor=DARK_GREY,
            alignment=TA_CENTER,
            leading=10,
        ))
        self.styles.add(ParagraphStyle(
            name="TableCellRight",
            fontName="Helvetica",
            fontSize=7.5,
            textColor=DARK_GREY,
            alignment=TA_RIGHT,
            leading=10,
        ))
        self.styles.add(ParagraphStyle(
            name="TotalLabel",
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=WHITE,
            alignment=TA_RIGHT,
            leading=14,
        ))
        self.styles.add(ParagraphStyle(
            name="TotalValue",
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=WHITE,
            alignment=TA_RIGHT,
            leading=14,
        ))
        self.styles.add(ParagraphStyle(
            name="TotalLabelSmall",
            fontName="Helvetica",
            fontSize=9,
            textColor=DARK_GREY,
            alignment=TA_RIGHT,
            leading=12,
        ))
        self.styles.add(ParagraphStyle(
            name="TotalValueSmall",
            fontName="Helvetica",
            fontSize=9,
            textColor=DARK_GREY,
            alignment=TA_RIGHT,
            leading=12,
        ))
        self.styles.add(ParagraphStyle(
            name="ExecutiveSummaryText",
            fontName="Helvetica",
            fontSize=9,
            textColor=DARK_GREY,
            leading=13,
        ))
        self.styles.add(ParagraphStyle(
            name="FooterText",
            fontName="Helvetica",
            fontSize=8,
            textColor=MID_GREY,
            leading=11,
        ))
        self.styles.add(ParagraphStyle(
            name="FooterContact",
            fontName="Helvetica",
            fontSize=7.5,
            textColor=MID_GREY,
            alignment=TA_CENTER,
            leading=10,
        ))
        self.styles.add(ParagraphStyle(
            name="FooterTimestamp",
            fontName="Helvetica-Oblique",
            fontSize=7,
            textColor=MID_GREY,
            alignment=TA_CENTER,
            leading=10,
        ))

    @staticmethod
    def _invoice_number(inv: dict) -> str:
        """Generate a human-readable invoice number."""
        tenant = inv.get("tenant_id", "unknown")
        ym     = inv.get("year_month", 0)
        prefix = tenant[:3].upper()
        return f"INV-{prefix}-{ym}"

    @staticmethod
    def _invoice_title(inv_data: dict) -> str:
        inv = inv_data.get("invoice", {})
        return f"Invoice {inv.get('tenant_id', '')} — {inv.get('year_month', '')}"

    @staticmethod
    def _month_name(year_month: int) -> str:
        """Convert 202603 → 'March 2026'."""
        year  = year_month // 100
        month = year_month % 100
        if 1 <= month <= 12:
            return f"{calendar.month_name[month]} {year}"
        return str(year_month)

    @staticmethod
    def _format_line_type(line_type: str) -> str:
        """Pretty-print line type for display."""
        mapping = {
            "base_fee":   "Base Fee",
            "usage_cost": "Usage",
            "overage":    "Overage",
            "credit":     "Credit",
            "premium":    "Premium",
        }
        return mapping.get(line_type, line_type.replace("_", " ").title())
