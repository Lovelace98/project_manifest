from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
import pytest

from report_bot.domain import CaseType
from report_bot.parser import parse_eurofins_report
from report_bot.pdf_service import (
    PERSONAL_WARNING,
    detect_report_font_family,
    encrypt_pdf,
    output_filename,
    password_for_case,
    rebrand_pdf,
)


def test_detected_report_font_family_and_compressed_branding(
    project_root: Path,
    pdf_fixtures: list[Path],
) -> None:
    for path in pdf_fixtures:
        source = path.read_bytes()
        parsed = parse_eurofins_report(source, path.name)
        assert detect_report_font_family(source) == "Arial"
        branded = rebrand_pdf(
            source,
            case_type=CaseType.CERTIFIED_LEGAL,
            test_type=parsed.test_type,
            logo_path=project_root / "blueprint_logo.png",
            contact_footer_path=project_root / "flow" / "static" / "assets" / "Contact PNG.png",
        )
        assert len(branded) < 300_000


@pytest.mark.parametrize("case_type", [CaseType.PERSONAL, CaseType.CERTIFIED_LEGAL])
def test_true_redaction_rebranding_preserves_scientific_content(
    project_root: Path,
    case_type: CaseType,
) -> None:
    source_path = project_root / "CD-KBDO-230926-03.pdf"
    parsed = parse_eurofins_report(source_path.read_bytes(), source_path.name)
    branded = rebrand_pdf(
        source_path.read_bytes(),
        case_type=case_type,
        test_type=parsed.test_type,
        logo_path=project_root / "blueprint_logo.png",
        contact_footer_path=project_root / "flow" / "static" / "assets" / "Contact PNG.png",
    )

    source = fitz.open(source_path)
    output = fitz.open(stream=branded, filetype="pdf")
    try:
        assert output.page_count == source.page_count == 2
        text = "\n".join(page.get_text() for page in output)
        assert "Eurofins Medigenomix" not in text
        assert "forensik@mgx" not in text
        assert "carries out all analyses" not in text
        title_prefix = "Personal" if case_type == CaseType.PERSONAL else "Legal"
        assert f"{title_prefix} Paternity Test Certificate" in text
        if case_type == CaseType.PERSONAL:
            assert PERSONAL_WARNING in " ".join(text.split())
            warning_spans = [
                span
                for b in output[0].get_text("dict")["blocks"]
                for line in b.get("lines", [])
                for span in line.get("spans", [])
                if PERSONAL_WARNING in span.get("text", "")
            ]
            assert len(warning_spans) == 1
            font_name = warning_spans[0]["font"].lower()
            assert "oblique" in font_name or "italic" in font_name
        else:
            assert PERSONAL_WARNING not in text

        for participant in parsed.participants:
            assert participant.full_name in text
        name_offsets = [text.index(participant.full_name) for participant in parsed.participants]
        assert name_offsets == sorted(name_offsets)
        assert "Results:" in text
        assert "+233 54 711 3957" in text
        assert "www.blueprintdna.org" in text
        assert "info@blueprintdna.org" in text
        assert all(len(page.get_images(full=True)) >= 1 for page in output)
        assert any(
            link.get("uri") == "http://www.blueprintdna.org"
            for page in output
            for link in page.get_links()
        )
    finally:
        source.close()
        output.close()


def test_all_fixture_headers_and_footers_are_removed(
    project_root: Path,
    pdf_fixtures: list[Path],
) -> None:
    for path in pdf_fixtures:
        parsed = parse_eurofins_report(path.read_bytes(), path.name)
        branded = rebrand_pdf(
            path.read_bytes(),
            case_type=CaseType.PERSONAL,
            test_type=parsed.test_type,
            logo_path=project_root / "blueprint_logo.png",
            contact_footer_path=project_root / "flow" / "static" / "assets" / "Contact PNG.png",
        )
        document = fitz.open(stream=branded, filetype="pdf")
        try:
            text = "\n".join(page.get_text() for page in document)
            assert "Eurofins Medigenomix" not in text
            assert "eurofinseu.com" not in text
            assert "location.eurofins.com" not in text
            assert "carries out all analyses" not in text
            assert "+233 54 711 3957" in text
            assert "www.blueprintdna.org" in text
            assert "info@blueprintdna.org" in text
        finally:
            document.close()


def test_rebranded_header_logo_background_is_pure_white(project_root: Path) -> None:
    path = project_root / "CD-EBD-210926-01.pdf"
    parsed = parse_eurofins_report(path.read_bytes(), path.name)
    branded = rebrand_pdf(
        path.read_bytes(),
        case_type=CaseType.CERTIFIED_LEGAL,
        test_type=parsed.test_type,
        logo_path=project_root / "blueprint_logo.png",
        contact_footer_path=project_root / "flow" / "static" / "assets" / "Contact PNG.png",
    )
    document = fitz.open(stream=branded, filetype="pdf")
    try:
        page = document[0]
        pix = page.get_pixmap(
            matrix=fitz.Matrix(4, 4),
            clip=fitz.Rect(46, 36, 69, 88),
            alpha=False,
        )
        assert pix.n == 3
        assert all(value == 255 for value in pix.samples)
    finally:
        document.close()


def test_aes_256_password_and_permissions(project_root: Path) -> None:
    path = project_root / "CD-EBD-210926-01.pdf"
    parsed = parse_eurofins_report(path.read_bytes(), path.name)
    branded = rebrand_pdf(
        path.read_bytes(),
        case_type=CaseType.PERSONAL,
        test_type=parsed.test_type,
        logo_path=project_root / "blueprint_logo.png",
        contact_footer_path=project_root / "flow" / "static" / "assets" / "Contact PNG.png",
    )
    user_password = password_for_case(parsed.case_code, "124bdo")
    assert user_password == "124bdo09"
    encrypted = encrypt_pdf(
        branded,
        user_password=user_password,
        owner_password="a-separate-owner-secret",
    )
    document = fitz.open(stream=encrypted, filetype="pdf")
    try:
        assert document.needs_pass
        assert not document.authenticate("wrong-password")
        assert document.authenticate(user_password)
        assert document.permissions & fitz.PDF_PERM_PRINT
        assert not document.permissions & fitz.PDF_PERM_COPY
        assert not document.permissions & fitz.PDF_PERM_MODIFY
        assert document.metadata.get("encryption") == "Standard V5 R6 256-bit AES"
    finally:
        document.close()


def test_output_filename_uses_primary_participant_surname(project_root: Path) -> None:
    path = project_root / "CD-EBD-210926-01.pdf"
    if path.exists():
        parsed = parse_eurofins_report(path.read_bytes(), path.name)
        assert (
            output_filename(parsed.case_code, parsed.test_type, parsed.participants)
            == "Appiah-Blueprint-CD-EBD-210926-01.pdf"
        )


def test_output_filename_with_suffix() -> None:
    from report_bot.domain import Participant
    participants = [
        Participant(
            sample_number="S1",
            role="ALLEGED MALE RELATIVE",
            full_name="JOSEPH AGYAPONG",
            date_of_birth="-",
            source_order=0,
        ),
        Participant(
            sample_number="S2",
            role="ALLEGED MALE RELATIVE",
            full_name="MOSES AGYAPONG",
            date_of_birth="-",
            source_order=1,
        ),
    ]
    assert (
        output_filename("CD-EBD-260926-03", "Y-CHROMOSOME", participants)
        == "Agyapong-Blueprint-CD-EBD-260926-03.pdf"
    )
    assert (
        output_filename("CD-EBD-260926-03", "Y-CHROMOSOME", participants, suffix="1")
        == "Agyapong-Blueprint-CD-EBD-260926-03_1.pdf"
    )
    assert (
        output_filename("CD-EBD-260926-03", "Y-CHROMOSOME", participants, suffix="2")
        == "Agyapong-Blueprint-CD-EBD-260926-03_2.pdf"
    )
