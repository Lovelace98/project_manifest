from __future__ import annotations

from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pymupdf as fitz
import pytest
from lxml import etree

from report_bot.domain import Participant
from report_bot.verification import (
    NS,
    LibreOfficeConverter,
    VerificationError,
    build_verification_docx,
    build_verification_pdfs,
)


def participants(count: int) -> list[Participant]:
    roles = ["ALLEGED FATHER", "MOTHER"] + ["CHILD"] * max(0, count - 2)
    return [
        Participant(
            sample_number=f"SAMPLE{index:03d}",
            role=roles[index] if index < len(roles) else "CHILD",
            full_name=f"A VERY LONG MULTI WORD PARTICIPANT NAME NUMBER {index}",
            date_of_birth="-" if index == count - 1 else "01/01/2000",
            source_order=index,
        )
        for index in range(count)
    ]


@pytest.mark.parametrize("count", [1, 2, 6])
def test_docx_is_patched_without_mutating_template(
    project_root: Path,
    tmp_path: Path,
    count: int,
) -> None:
    template = project_root / "verification page template.docx"
    before = template.read_bytes()
    output = tmp_path / f"verification-{count}.docx"
    build_verification_docx(
        template,
        output,
        case_code="cd-test-011026-01",
        collection_date=date(2026, 10, 1),
        participants=participants(count),
    )
    assert template.read_bytes() == before

    with ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml")
        footer_xml = archive.read("word/footer1.xml")
        content_types = archive.read("[Content_Types].xml")
    document = etree.fromstring(document_xml)
    tables = document.xpath("//w:tbl", namespaces=NS)
    rows = tables[1].findall("w:tr", NS)
    text = " ".join(document.xpath("//w:t/text()", namespaces=NS))
    footer_text = "".join(etree.fromstring(footer_xml).xpath(".//w:t/text()", namespaces=NS))

    assert len(rows) == count + 1
    assert "CD-TEST-011026-01" in text
    assert "BUCCAL SWAB" in text
    assert "01/10/2026" in text
    assert "{SO YOU ADD NEW ROWS" not in text
    assert all(person.full_name.upper() in text for person in participants(count))
    assert "+233 54 711 3957" in footer_text
    assert "www.blueprintdna.org" in footer_text
    assert "----" in footer_text
    assert b"wordprocessingml.document.main+xml" in content_types


def test_verification_font_matches_profile_and_footer_is_small_nonbold(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "font-profile.docx"
    build_verification_docx(
        project_root / "verification page template.docx",
        output,
        case_code="CD-TEST-011026-01",
        collection_date=date(2026, 10, 1),
        participants=participants(2),
        font_family="Times New Roman",
    )
    with ZipFile(output) as archive:
        document = etree.fromstring(archive.read("word/document.xml"))
        footer = etree.fromstring(archive.read("word/footer1.xml"))

    assert set(document.xpath("//w:rFonts/@w:ascii", namespaces=NS)) == {"Times New Roman"}
    assert set(footer.xpath("//w:rFonts/@w:ascii", namespaces=NS)) == {"Times New Roman"}
    contact = next(
        paragraph
        for paragraph in footer.xpath("//w:p", namespaces=NS)
        if "blueprintdna.org" in "".join(
            paragraph.xpath(".//w:t/text()", namespaces=NS)
        ).lower()
    )
    assert not contact.xpath(".//w:b | .//w:bCs", namespaces=NS)
    assert set(contact.xpath(".//w:sz/@w:val | .//w:szCs/@w:val", namespaces=NS)) == {"20"}


class FakeConverter:
    def convert(self, _docx_path: Path, pdf_path: Path) -> Path:
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "VERIFICATION PAGE")
        document.save(pdf_path)
        document.close()
        return pdf_path


def test_eight_participants_create_two_ordered_verification_pages(
    project_root: Path,
    tmp_path: Path,
) -> None:
    source = participants(8)
    source[0].role = "CHILD"
    source[1].role = "ALLEGED FATHER"
    source[2].role = "MOTHER"
    output = build_verification_pdfs(
        template_path=project_root / "verification page template.docx",
        work_dir=tmp_path,
        case_code="CD-TEST-011026-01",
        collection_date=date(2026, 10, 1),
        test_type="PATERNITY",
        participants=source,
        converter=FakeConverter(),
    )
    assert [path.name for path in output] == ["verification-1.pdf", "verification-2.pdf"]
    for path in output:
        document = fitz.open(path)
        assert document.page_count == 1
        document.close()

    with ZipFile(tmp_path / "verification-1.docx") as archive:
        text = " ".join(
            etree.fromstring(archive.read("word/document.xml")).xpath(
                "//w:t/text()",
                namespaces=NS,
            )
        )
    assert source[1].full_name in text
    assert source[2].full_name in text
    assert text.index(source[1].full_name) < text.index(source[2].full_name)
    assert text.index(source[2].full_name) < text.index(source[0].full_name)


def test_converter_fails_closed_when_libreoffice_is_unavailable(tmp_path: Path) -> None:
    converter = LibreOfficeConverter(executable="")
    converter.executable = None
    with pytest.raises(VerificationError, match="LibreOffice is required"):
        converter.convert(tmp_path / "input.docx", tmp_path / "output.pdf")


def test_long_participant_names_strip_empty_spacers(
    project_root: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "verification-long-names.docx"
    case_participants = [
        Participant(
            sample_number="VAT261015_019",
            role="ALLEGED FATHER",
            full_name="JOSEPH BOAMPONG",
            date_of_birth="28/08/1981",
            source_order=0,
        ),
        Participant(
            sample_number="VAT261015_020",
            role="CHILD",
            full_name="JOYCE TWENEBOAH KODUAH BOAMPONG",
            date_of_birth="30/11/2025",
            source_order=1,
        ),
        Participant(
            sample_number="VAT261015_022",
            role="CHILD",
            full_name="JOCELYN TWENEBOAH KODUAH BOAMPONG",
            date_of_birth="30/11/2025",
            source_order=2,
        ),
    ]

    build_verification_docx(
        project_root / "verification page template.docx",
        output,
        case_code="CD-KBDO-121026-05",
        collection_date=date(2026, 10, 12),
        participants=case_participants,
    )

    with ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml")
    document = etree.fromstring(document_xml)
    body = document.find("w:body", NS)

    # Verify no empty spacer paragraphs remain in the body
    empty_paragraphs = [
        p
        for p in body.findall("w:p", NS)
        if not "".join(p.xpath(".//w:t/text()", namespaces=NS)).strip()
        and not p.xpath(".//w:drawing | .//w:pict", namespaces=NS)
    ]
    assert len(empty_paragraphs) == 0

    # If LibreOffice converter is available in environment, test actual PDF conversion
    converter = LibreOfficeConverter()
    if converter.available:
        pdf_path = tmp_path / "verification-long-names.pdf"
        converter.convert(output, pdf_path)
        doc = fitz.open(pdf_path)
        assert doc.page_count == 1
        doc.close()
