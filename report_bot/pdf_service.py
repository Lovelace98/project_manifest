from __future__ import annotations

import io
import re
from collections import Counter
from pathlib import Path

import pymupdf as fitz
from PIL import Image

from report_bot.domain import CaseType, Participant
from report_bot.images import clear_near_white_background

PERSONAL_WARNING = "PERSONAL USE ONLY - NOT AN OFFICIAL DOCUMENT"
CONTACT_FOOTER_TEXT = (
    "+233 54 711 3957 ---- www.blueprintdna.org ---- info@blueprintdna.org"
)
HEADER_LOGO_WIDTH = 230.7
HEADER_LOGO_HEIGHT = 54.85
HEADER_LOGO_LEFT = 44.95
HEADER_LOGO_TOP = 35.0
TITLE_PHRASES = (
    "Personal Paternity Test Certificate",
    "Personal Maternity Test Certificate",
    "Personal Siblingship Test Certificate",
    "Personal Avuncular Test Certificate",
    "Personal Grandparentage Test Certificate",
    "Personal Y-Chromosome Test Certificate",
    "Personal Y Chromosome Test Certificate",
    "Personal Mitochondrial Test Certificate",
    "Personal Zygosity Test Certificate",
    "Personal Test Certificate",
    "Legal Paternity Test Certificate",
    "Legal Maternity Test Certificate",
    "Legal Siblingship Test Certificate",
    "Legal Avuncular Test Certificate",
    "Legal Grandparentage Test Certificate",
    "Legal Y-Chromosome Test Certificate",
    "Legal Y Chromosome Test Certificate",
    "Legal Mitochondrial Test Certificate",
    "Legal Zygosity Test Certificate",
    "Legal Test Certificate",
    "Paternity Test Certificate",
    "Maternity Test Certificate",
    "Siblingship Test Certificate",
    "Avuncular Test Certificate",
    "Grandparentage Test Certificate",
    "Y-Chromosome Test Certificate",
    "Y Chromosome Test Certificate",
    "Mitochondrial Test Certificate",
    "Zygosity Test Certificate",
    "Test Certificate",
)


class PdfProcessingError(RuntimeError):
    pass


_FONT_FAMILY_ALIASES = (
    ("arial", "Arial"),
    ("helvetica", "Arial"),
    ("liberation sans", "Arial"),
    ("times", "Times New Roman"),
    ("cambria", "Cambria"),
    ("calibri", "Calibri"),
    ("open sans", "Open Sans"),
)


def _normalize_font_family(font_name: str) -> str | None:
    normalized = font_name.split("+")[-1].replace("_", " ").lower()
    for marker, family in _FONT_FAMILY_ALIASES:
        if marker in normalized:
            return family
    return None


def detect_report_font_family(pdf_bytes: bytes) -> str:
    """Return the dominant text font family as a Word-compatible family name."""
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    counts: Counter[str] = Counter()
    try:
        for page in document:
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        family = _normalize_font_family(str(span.get("font", "")))
                        text = str(span.get("text", "")).strip()
                        if family and text:
                            counts[family] += len(text)
    finally:
        document.close()
    return counts.most_common(1)[0][0] if counts else "Arial"


def _optimized_image_stream(path: Path, target_rect: fitz.Rect) -> bytes:
    """Downsample a source image to print quality with clean transparency and compression."""
    with Image.open(path) as image:
        image.load()
        scale = 250.0 / 72.0
        target_w = max(1, round(target_rect.width * scale))
        target_h = max(1, round(target_rect.height * scale))
        image.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
        cleaned = clear_near_white_background(image)
        quantized = cleaned.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
        output = io.BytesIO()
        quantized.save(
            output,
            format="PNG",
            optimize=True,
            compress_level=9,
        )
        return output.getvalue()


def _union_rectangles(rectangles: list[fitz.Rect]) -> fitz.Rect:
    result = fitz.Rect(rectangles[0])
    for rectangle in rectangles[1:]:
        result |= rectangle
    return result


def _find_title_rect(page: fitz.Page) -> fitz.Rect:
    for phrase in TITLE_PHRASES:
        hits = page.search_for(phrase)
        if hits:
            rect = _union_rectangles(hits)
            return fitz.Rect(
                max(0, rect.x0 - 22),
                rect.y0 - 8,
                min(page.rect.width, rect.x1 + 22),
                rect.y1 + 8,
            )

    candidates: list[fitz.Rect] = []
    page_dict = page.get_text("dict")
    for block in page_dict.get("blocks", []):
        for line in block.get("lines", []):
            line_text = "".join(span.get("text", "") for span in line.get("spans", []))
            if "TEST CERTIFICATE" in line_text.upper():
                candidates.extend(fitz.Rect(span["bbox"]) for span in line.get("spans", []))
    if candidates:
        rect = _union_rectangles(candidates)
        return fitz.Rect(
            max(0, rect.x0 - 22),
            rect.y0 - 8,
            min(page.rect.width, rect.x1 + 22),
            rect.y1 + 8,
        )
    raise PdfProcessingError("Certificate title could not be located")


def _new_title(case_type: CaseType, test_type: str) -> str:
    normalized = test_type.strip().title()
    if normalized.upper() == "Y-CHROMOSOME":
        normalized = "Y-Chromosome"
    prefix = "Personal" if case_type == CaseType.PERSONAL else "Legal"
    return f"{prefix} {normalized} Test Certificate"


def rebrand_pdf(
    pdf_bytes: bytes,
    *,
    case_type: CaseType,
    test_type: str,
    logo_path: Path,
    contact_footer_path: Path | None = None,
) -> bytes:
    if not logo_path.exists():
        raise PdfProcessingError("Blueprint logo asset is missing")

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        first_logo_rect = fitz.Rect(
            HEADER_LOGO_LEFT,
            HEADER_LOGO_TOP,
            HEADER_LOGO_LEFT + HEADER_LOGO_WIDTH,
            HEADER_LOGO_TOP + HEADER_LOGO_HEIGHT,
        )
        optimized_logo = _optimized_image_stream(logo_path, first_logo_rect)
        logo_xref: int | None = None
        title_rect: fitz.Rect | None = None
        for page_index, page in enumerate(document):
            page_width = page.rect.width
            page_height = page.rect.height

            header_rect = fitz.Rect(55, 24, min(page_width - 40, 540), 101)
            page.add_redact_annot(header_rect, fill=(1, 1, 1))

            disclaimer_hits = page.search_for("carries out all analyses")
            if disclaimer_hits:
                footer_y = max(0, min(hit.y0 for hit in disclaimer_hits) - 1)
            else:
                footer_y = max(0, page_height - 92)
            footer_rect = fitz.Rect(0, footer_y, page_width, page_height)
            page.add_redact_annot(footer_rect, fill=(1, 1, 1))

            if page_index == 0:
                title_rect = _find_title_rect(page)
                page.add_redact_annot(title_rect, fill=(1, 1, 1))

            page.apply_redactions(
                images=getattr(fitz, "PDF_REDACT_IMAGE_REMOVE", 1),
                graphics=getattr(fitz, "PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED", 2),
                text=getattr(fitz, "PDF_REDACT_TEXT_REMOVE", 0),
            )

            logo_rect = fitz.Rect(
                HEADER_LOGO_LEFT,
                HEADER_LOGO_TOP,
                min(page_width - 52, HEADER_LOGO_LEFT + HEADER_LOGO_WIDTH),
                HEADER_LOGO_TOP + HEADER_LOGO_HEIGHT,
            )
            if logo_xref is None:
                logo_xref = page.insert_image(
                    logo_rect,
                    stream=optimized_logo,
                    keep_proportion=True,
                    overlay=True,
                )
            else:
                page.insert_image(
                    logo_rect,
                    xref=logo_xref,
                    keep_proportion=True,
                    overlay=True,
                )

            footer_text_rect = fitz.Rect(0, page_height - 38, page_width, page_height - 18)
            page.insert_textbox(
                footer_text_rect,
                CONTACT_FOOTER_TEXT,
                fontsize=10,
                fontname="helv",
                color=(0, 0, 0),
                align=fitz.TEXT_ALIGN_CENTER,
                overlay=True,
            )
            for hit in page.search_for("www.blueprintdna.org"):
                if hit.y0 >= page_height - 60:
                    page.insert_link({
                        "kind": fitz.LINK_URI,
                        "uri": "http://www.blueprintdna.org",
                        "from": hit,
                    })

            if page_index == 0 and title_rect is not None:
                insertion_rect = fitz.Rect(0, title_rect.y0, page_width, title_rect.y1 + 2)
                remaining = page.insert_textbox(
                    insertion_rect,
                    _new_title(case_type, test_type),
                    fontsize=15,
                    fontname="hebo",
                    color=(0, 0, 0),
                    align=fitz.TEXT_ALIGN_CENTER,
                    overlay=True,
                )
                if remaining < 0:
                    raise PdfProcessingError("Replacement certificate title did not fit")

                if case_type == CaseType.PERSONAL:
                    result_hits = page.search_for("Results:")
                    if not result_hits:
                        raise PdfProcessingError(
                            "Results heading was not found for personal warning"
                        )
                    anchor = result_hits[0]
                    warning_rect = fitz.Rect(
                        anchor.x1 + 10,
                        anchor.y0 - 3,
                        page_width - 55,
                        anchor.y1 + 7,
                    )
                    remaining = page.insert_textbox(
                        warning_rect,
                        PERSONAL_WARNING,
                        fontsize=12,
                        fontname="heit",
                        color=(0.85, 0.05, 0.05),
                        align=fitz.TEXT_ALIGN_LEFT,
                        overlay=True,
                    )
                    if remaining < 0:
                        raise PdfProcessingError("Personal-use warning did not fit")

        output = io.BytesIO()
        document.save(
            output,
            garbage=4,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
        )
        return output.getvalue()
    finally:
        document.close()


UFC_LOGO_INSERT_RECT = fitz.Rect(42, 36, 252, 96)
UFC_LOGO_REDACT_RECT = fitz.Rect(34, 30, 255, 96)
UFC_NO_REDACT_RECT = fitz.Rect(466, 55, 485.0, 70)
UFC_PERSONAL_WARNING_RECT = fitz.Rect(0, 158.0, 612, 179.0)


def rebrand_ufc_pdf(
    pdf_bytes: bytes,
    *,
    case_type: CaseType,
    test_type: str,
    logo_path: Path,
) -> bytes:
    if not logo_path.exists():
        raise PdfProcessingError("Blueprint logo asset is missing")

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if document.page_count < 1:
            raise PdfProcessingError("UFC PDF contains no pages")
        page = document[0]
        page_width = page.rect.width
        page_height = page.rect.height

        # 1. Redact low-quality logo
        page.add_redact_annot(UFC_LOGO_REDACT_RECT, fill=(1, 1, 1))

        # 2. Redact UFC footer
        footer_hits = page.search_for("Testing performed by Universal Forensics")
        if footer_hits:
            footer_y = max(0, min(hit.y0 for hit in footer_hits) - 2)
        else:
            footer_y = max(0, page_height - 60)
        page.add_redact_annot(fitz.Rect(0, footer_y, page_width, page_height), fill=(1, 1, 1))

        # 3. For Certified / Legal: redact only "No" from "No Chain of Custody"
        if case_type == CaseType.CERTIFIED_LEGAL:
            words = page.get_text("words")
            no_word = next(
                (w for w in words if w[4] == "No" and 50 <= w[1] <= 75 and w[0] > 450),
                None,
            )
            if no_word:
                no_rect = fitz.Rect(no_word[0] - 1, no_word[1] - 1, no_word[2] + 1, no_word[3] + 1)
            else:
                no_rect = UFC_NO_REDACT_RECT
            page.add_redact_annot(no_rect, fill=(1, 1, 1))

        # Apply redactions
        page.apply_redactions(
            images=getattr(fitz, "PDF_REDACT_IMAGE_REMOVE", 1),
            graphics=getattr(fitz, "PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED", 2),
            text=getattr(fitz, "PDF_REDACT_TEXT_REMOVE", 0),
        )

        # 4. Insert high-quality Blueprint logo
        optimized_logo = _optimized_image_stream(logo_path, UFC_LOGO_INSERT_RECT)
        page.insert_image(
            UFC_LOGO_INSERT_RECT,
            stream=optimized_logo,
            keep_proportion=True,
            overlay=True,
        )

        # 5. Insert Blueprint contact footer
        footer_text_rect = fitz.Rect(0, page_height - 38, page_width, page_height - 18)
        page.insert_textbox(
            footer_text_rect,
            CONTACT_FOOTER_TEXT,
            fontsize=10,
            fontname="helv",
            color=(0, 0, 0),
            align=fitz.TEXT_ALIGN_CENTER,
            overlay=True,
        )
        for hit in page.search_for("www.blueprintdna.org"):
            if hit.y0 >= page_height - 60:
                page.insert_link({
                    "kind": fitz.LINK_URI,
                    "uri": "http://www.blueprintdna.org",
                    "from": hit,
                })

        # 6. For Personal: insert red warning
        if case_type == CaseType.PERSONAL:
            remaining = page.insert_textbox(
                UFC_PERSONAL_WARNING_RECT,
                PERSONAL_WARNING,
                fontsize=12,
                fontname="heit",
                color=(0.85, 0.05, 0.05),
                align=fitz.TEXT_ALIGN_CENTER,
                overlay=True,
            )
            if remaining < 0:
                raise PdfProcessingError("Personal-use warning did not fit")

        output = io.BytesIO()
        document.save(
            output,
            garbage=4,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
        )
        return output.getvalue()
    finally:
        document.close()


def merge_pdf_bytes(documents: list[bytes]) -> bytes:
    if not documents:
        raise PdfProcessingError("No PDF documents were supplied for merging")
    merged = fitz.open()
    try:
        for pdf_bytes in documents:
            source = fitz.open(stream=pdf_bytes, filetype="pdf")
            try:
                merged.insert_pdf(source)
            finally:
                source.close()
        output = io.BytesIO()
        merged.save(
            output,
            garbage=4,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
        )
        return output.getvalue()
    finally:
        merged.close()


def password_for_case(case_code: str, prefix: str) -> str:
    parts = case_code.split("-")
    if len(parts) < 3 or len(parts[-2]) != 6 or not parts[-2].isdigit():
        raise PdfProcessingError("Case reference does not contain a valid DDMMYY date")
    month = parts[-2][2:4]
    if not 1 <= int(month) <= 12:
        raise PdfProcessingError("Case reference contains an invalid password month")
    return f"{prefix}{month}"


def encrypt_pdf(
    pdf_bytes: bytes,
    *,
    user_password: str,
    owner_password: str,
) -> bytes:
    if not owner_password:
        raise PdfProcessingError("PDF owner password is not configured")
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        output = io.BytesIO()
        document.save(
            output,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            user_pw=user_password,
            owner_pw=owner_password,
            permissions=fitz.PDF_PERM_PRINT,
            garbage=4,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
        )
        return output.getvalue()
    finally:
        document.close()


def output_filename(
    case_code: str,
    test_type: str,
    participants: list[Participant],
    *,
    suffix: str | int | None = None,
) -> str:
    if not participants:
        surname = "REPORT"
    else:
        from report_bot.ordering import order_participants

        ordered = order_participants(test_type, participants)
        tokens = ordered[0].full_name.split()
        surname = tokens[-1] if tokens else "REPORT"
    safe_surname = re.sub(r"[^A-Z0-9_-]+", "_", surname.upper()).strip("_") or "REPORT"
    base = f"{safe_surname.title()}-Blueprint-{case_code.upper()}"
    if suffix is not None and str(suffix).strip():
        return f"{base}_{suffix}.pdf"
    return f"{base}.pdf"
