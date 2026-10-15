from __future__ import annotations

import shutil
import subprocess
import tempfile
from copy import deepcopy
from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pymupdf as fitz
from lxml import etree

from report_bot.domain import Participant
from report_bot.ordering import order_participants

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"w": WORD_NS, "ct": CONTENT_TYPES_NS}
SUPPORTED_FONT_FAMILIES = {
    "Arial",
    "Calibri",
    "Cambria",
    "Open Sans",
    "Times New Roman",
}
CONTACT_FOOTER_FONT_SIZE = "20"
CONTACT_FOOTER_TEXT = (
    "+233 54 711 3957 ---- www.blueprintdna.org ---- info@blueprintdna.org"
)
LEGACY_CONTACT_LINE = CONTACT_FOOTER_TEXT


class VerificationError(RuntimeError):
    pass


def _set_element_text(container: etree._Element, value: str) -> None:
    texts = container.xpath(".//w:t", namespaces=NS)
    if not texts:
        raise VerificationError("Template slot contains no text node")
    texts[0].text = value
    for extra in texts[1:]:
        extra.text = ""


def _set_run_font_family(root: etree._Element, font_family: str) -> None:
    family = font_family if font_family in SUPPORTED_FONT_FAMILIES else "Arial"
    for run in root.xpath(".//w:r", namespaces=NS):
        properties = run.find("w:rPr", NS)
        if properties is None:
            properties = etree.Element(f"{{{WORD_NS}}}rPr")
            run.insert(0, properties)
        fonts = properties.find("w:rFonts", NS)
        if fonts is None:
            fonts = etree.SubElement(properties, f"{{{WORD_NS}}}rFonts")
        for attribute in ("ascii", "hAnsi", "cs", "eastAsia"):
            fonts.set(f"{{{WORD_NS}}}{attribute}", family)

    for properties in root.xpath(".//w:rPr", namespaces=NS):
        fonts = properties.find("w:rFonts", NS)
        if fonts is None:
            fonts = etree.SubElement(properties, f"{{{WORD_NS}}}rFonts")
        for attribute in ("ascii", "hAnsi", "cs", "eastAsia"):
            fonts.set(f"{{{WORD_NS}}}{attribute}", family)


def _set_contact_footer_style(paragraph: etree._Element) -> None:
    for bold in paragraph.xpath(".//w:b | .//w:bCs", namespaces=NS):
        parent = bold.getparent()
        if parent is not None:
            parent.remove(bold)

    properties = paragraph.find("w:pPr", NS)
    if properties is None:
        properties = etree.Element(f"{{{WORD_NS}}}pPr")
        paragraph.insert(0, properties)
    paragraph_run_properties = properties.find("w:rPr", NS)
    if paragraph_run_properties is None:
        paragraph_run_properties = etree.SubElement(properties, f"{{{WORD_NS}}}rPr")

    run_properties_list = [
        paragraph_run_properties,
        *paragraph.xpath(".//w:r/w:rPr", namespaces=NS),
    ]
    for run_properties in run_properties_list:
        for size in run_properties.xpath("./w:sz | ./w:szCs", namespaces=NS):
            run_properties.remove(size)
        for tag in ("sz", "szCs"):
            size = etree.SubElement(run_properties, f"{{{WORD_NS}}}{tag}")
            size.set(f"{{{WORD_NS}}}val", CONTACT_FOOTER_FONT_SIZE)


def _compact_participant_row(row: etree._Element, participant_count: int) -> None:
    if participant_count < 5:
        return
    for margin in row.xpath(".//w:tcMar/w:top | .//w:tcMar/w:bottom", namespaces=NS):
        margin.set(f"{{{WORD_NS}}}w", "20")
    for size in row.xpath(".//w:sz | .//w:szCs", namespaces=NS):
        size.set(f"{{{WORD_NS}}}val", "15")
    for paragraph_properties in row.xpath(".//w:pPr", namespaces=NS):
        spacing = paragraph_properties.find("w:spacing", NS)
        if spacing is None:
            spacing = etree.SubElement(
                paragraph_properties,
                f"{{{WORD_NS}}}spacing",
            )
        spacing.set(f"{{{WORD_NS}}}before", "0")
        spacing.set(f"{{{WORD_NS}}}after", "0")
        spacing.set(f"{{{WORD_NS}}}line", "180")
        spacing.set(f"{{{WORD_NS}}}lineRule", "atLeast")


def _convert_template_content_type(parts: dict[str, bytes]) -> None:
    root = etree.fromstring(parts["[Content_Types].xml"])
    for override in root.findall("ct:Override", NS):
        if override.get("PartName") == "/word/document.xml":
            override.set(
                "ContentType",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
            )
            break
    parts["[Content_Types].xml"] = etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )


def build_verification_docx(
    template_path: Path,
    output_path: Path,
    *,
    case_code: str,
    collection_date: date,
    participants: list[Participant],
    font_family: str = "Arial",
) -> Path:
    if not 1 <= len(participants) <= 6:
        raise VerificationError("A verification page requires between one and six participants")
    if not template_path.exists():
        raise VerificationError("Verification template is missing")

    with ZipFile(template_path) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    _convert_template_content_type(parts)

    document = etree.fromstring(parts["word/document.xml"])
    body = document.find("w:body", NS)
    if body is None:
        raise VerificationError("Verification template body is missing")

    for paragraph in list(body.findall("w:p", NS)):
        paragraph_text = "".join(paragraph.xpath(".//w:t/text()", namespaces=NS)).strip()
        if paragraph_text.startswith("{SO YOU ADD NEW ROWS"):
            body.remove(paragraph)

    case_slots = [
        node
        for node in document.xpath("//w:t", namespaces=NS)
        if "{CASE CODE}" in (node.text or "")
    ]
    if len(case_slots) != 1:
        raise VerificationError("Verification template case-code slot is invalid")
    case_slots[0].text = (case_slots[0].text or "").replace(
        "{CASE CODE}", case_code.upper()
    )

    tables = body.findall("w:tbl", NS)
    if len(tables) < 2:
        raise VerificationError("Verification participant table is missing")
    participant_table = tables[1]
    rows = participant_table.findall("w:tr", NS)
    if len(rows) < 3:
        raise VerificationError("Verification participant row prototypes are missing")

    header = rows[0]
    prototype = rows[1]
    for row in rows[1:]:
        participant_table.remove(row)

    formatted_date = collection_date.strftime("%d/%m/%Y")
    for participant in participants:
        row = deepcopy(prototype)
        _compact_participant_row(row, len(participants))
        cells = row.findall("w:tc", NS)
        if len(cells) != 4:
            raise VerificationError("Verification participant row must have four cells")
        _set_element_text(cells[0], participant.full_name.upper())
        _set_element_text(cells[1], participant.role.upper())
        _set_element_text(cells[2], "BUCCAL SWAB")
        _set_element_text(cells[3], formatted_date)
        participant_table.append(row)

    if participant_table.findall("w:tr", NS)[0] is not header:
        raise VerificationError("Verification table header changed unexpectedly")

    footer = etree.fromstring(parts["word/footer1.xml"])
    footer_paragraphs = footer.xpath("//w:p", namespaces=NS)
    target = next(
        (
            paragraph
            for paragraph in footer_paragraphs
            if "blueprintdna.org" in "".join(
                paragraph.xpath(".//w:t/text()", namespaces=NS)
            ).lower()
        ),
        None,
    )
    if target is None:
        raise VerificationError("Verification footer contact slot is missing")
    existing_footer_text = "".join(target.xpath(".//w:t/text()", namespaces=NS))
    if "----" not in existing_footer_text:
        _set_element_text(target, CONTACT_FOOTER_TEXT)
    _set_contact_footer_style(target)

    parts["word/document.xml"] = etree.tostring(
        document,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )
    parts["word/footer1.xml"] = etree.tostring(
        footer,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )

    for name, data in list(parts.items()):
        if name.startswith("word/") and name.endswith(".xml"):
            root = etree.fromstring(data)
            _set_run_font_family(root, font_family)
            parts[name] = etree.tostring(
                root,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output_path, "w", ZIP_DEFLATED) as destination:
        for name, data in parts.items():
            destination.writestr(name, data)
    output_path.chmod(0o600)
    return output_path


class LibreOfficeConverter:
    def __init__(self, executable: str | None = None):
        self.executable = executable or self._find_executable()

    @staticmethod
    def _find_executable() -> str | None:
        candidates = (
            shutil.which("soffice"),
            shutil.which("libreoffice"),
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        )
        return next(
            (
                candidate
                for candidate in candidates
                if candidate and Path(candidate).exists()
            ),
            None,
        )

    @property
    def available(self) -> bool:
        return bool(self.executable)

    def convert(self, docx_path: Path, pdf_path: Path) -> Path:
        if not self.executable:
            raise VerificationError("LibreOffice is required for verification-page conversion")

        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="report-bot-libreoffice-") as temp_dir:
            temporary_root = Path(temp_dir)
            profile = temporary_root / "profile"
            profile.mkdir()
            command = [
                self.executable,
                "--headless",
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(pdf_path.parent),
                str(docx_path),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            generated = pdf_path.parent / f"{docx_path.stem}.pdf"
            if completed.returncode != 0 or not generated.exists():
                raise VerificationError("LibreOffice could not convert the verification page")
            if generated != pdf_path:
                generated.replace(pdf_path)

        pdf_document = fitz.open(pdf_path)
        try:
            if pdf_document.page_count != 1:
                raise VerificationError(
                    "Each verification document must render as exactly one page"
                )
        finally:
            pdf_document.close()
        pdf_path.chmod(0o600)
        return pdf_path


def build_verification_pdfs(
    *,
    template_path: Path,
    work_dir: Path,
    case_code: str,
    collection_date: date,
    test_type: str,
    participants: list[Participant],
    converter: LibreOfficeConverter,
    font_family: str = "Arial",
) -> list[Path]:
    ordered = order_participants(test_type, participants)
    chunks = [ordered[index : index + 6] for index in range(0, len(ordered), 6)]
    outputs: list[Path] = []
    for page_number, chunk in enumerate(chunks, start=1):
        docx_path = work_dir / f"verification-{page_number}.docx"
        pdf_path = work_dir / f"verification-{page_number}.pdf"
        build_verification_docx(
            template_path,
            docx_path,
            case_code=case_code,
            collection_date=collection_date,
            participants=chunk,
            font_family=font_family,
        )
        converter.convert(docx_path, pdf_path)
        outputs.append(pdf_path)
    return outputs
