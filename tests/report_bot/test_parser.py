from __future__ import annotations

import io
from pathlib import Path

import pymupdf as fitz
import pytest

from report_bot.parser import ReportParseError, parse_eurofins_report

EXPECTED = {
    "CD-EBD-210926-01.pdf": [
        ("HID260924_096", "ALLEGED FATHER", "KENNEDY AKWASI APPIAH", "02/05/1974"),
        ("HID260924_097", "CHILD", "DERRICK THOMAS MANU", "20/10/2008"),
    ],
    "CD-EBD-220926-01.pdf": [
        ("HID260924_063", "ALLEGED FATHER", "MASANI MOHAMMED KUGBLENU", "07/06/1998"),
        ("HID260924_064", "CHILD", "HUBERT MERRICK AFLOE", "20/06/2026"),
    ],
    "CD-KBDO-220926-01.pdf": [
        ("HID260928_060", "ALLEGED FATHER", "THOMAS KOFI BOAKYE", "09/10/2007"),
        ("HID260928_061", "CHILD", "NANA YAW", "-"),
    ],
    "CD-KBDO-230926-03.pdf": [
        ("VAT260928_070", "ALLEGED FATHER", "ABDUL SAMED SAEED", "15/05/1975"),
        ("VAT260928_071", "CHILD", "SHAMSIYA ABDUL SAMED", "22/10/2008"),
        ("VAT260928_072", "CHILD", "SAEED ABDUL SAMED", "29/05/2017"),
    ],
}


def test_all_supplied_reports_parse_exactly(pdf_fixtures: list[Path]) -> None:
    assert len(pdf_fixtures) == 4
    participant_total = 0
    page_counts: list[int] = []
    all_source_text = ""

    for path in pdf_fixtures:
        parsed = parse_eurofins_report(path.read_bytes(), path.name)
        participant_total += len(parsed.participants)
        assert parsed.case_code == path.stem
        assert parsed.test_type == "PATERNITY"
        assert not parsed.blocking_errors
        assert [
            (
                participant.sample_number,
                participant.role,
                participant.full_name,
                participant.date_of_birth,
            )
            for participant in parsed.participants
        ] == EXPECTED[path.name]
        assert parsed.case_code == parsed.case_code.upper()
        assert parsed.test_type == parsed.test_type.upper()
        assert all(
            participant.full_name == participant.full_name.upper()
            and participant.role == participant.role.upper()
            and participant.sample_number == participant.sample_number.upper()
            for participant in parsed.participants
        )

        document = fitz.open(path)
        try:
            page_counts.append(document.page_count)
            all_source_text += "\n".join(page.get_text() for page in document)
        finally:
            document.close()

    assert participant_total == 9
    assert sorted(page_counts) == [1, 1, 1, 2]
    assert "excluded" in all_source_text.lower()
    assert "probability of" in all_source_text.lower()
    assert "Personal Paternity Test Certificate" in all_source_text
    assert "Personal Test Certificate" in all_source_text


def test_missing_date_of_birth_remains_hyphen(project_root: Path) -> None:
    path = project_root / "CD-KBDO-220926-01.pdf"
    parsed = parse_eurofins_report(path.read_bytes(), path.name)
    assert parsed.participants[1].date_of_birth == "-"
    assert parsed.confidence == pytest.approx(0.9)
    assert parsed.warnings == [
        "One or more participants have no date of birth in the source report"
    ]


def test_filename_case_mismatch_is_blocking(project_root: Path) -> None:
    path = project_root / "CD-EBD-210926-01.pdf"
    parsed = parse_eurofins_report(path.read_bytes(), "CD-EBD-220926-01.pdf")
    assert "Filename case code does not match" in parsed.blocking_errors[0]
    assert parsed.confidence <= 0.5


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not-a-pdf", "valid PDF header"),
        (b"%PDF-oversized", "25 MB file limit"),
    ],
)
def test_invalid_signatures_and_sizes_are_rejected(content: bytes, message: str) -> None:
    kwargs = {"max_file_bytes": 4} if "oversized" in content.decode() else {}
    with pytest.raises(ReportParseError, match=message):
        parse_eurofins_report(content, "report.pdf", **kwargs)


def test_image_only_pdf_is_quarantined() -> None:
    document = fitz.open()
    document.new_page()
    payload = document.tobytes()
    document.close()
    with pytest.raises(ReportParseError, match="image-only"):
        parse_eurofins_report(payload, "scan.pdf")


def test_encrypted_input_is_rejected() -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "encrypted input report")
    output = io.BytesIO()
    document.save(
        output,
        encryption=fitz.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        owner_pw="owner-secret",
    )
    document.close()
    with pytest.raises(ReportParseError, match="Encrypted input"):
        parse_eurofins_report(output.getvalue(), "encrypted.pdf")


def test_participant_tables_can_continue_on_later_pages(project_root: Path) -> None:
    source = project_root / "CD-EBD-210926-01.pdf"
    document = fitz.open(source)
    page = document.new_page()
    for x, value in ((72, "SAMPLE"), (180, "ROLE"), (300, "NAME"), (480, "DATE")):
        page.insert_text((x, 100), value, fontsize=9)
    page.insert_text((72, 125), "HID261001_001", fontsize=9)
    page.insert_text((180, 125), "CHILD", fontsize=9)
    page.insert_text((300, 125), "SECOND PAGE PERSON", fontsize=9)
    page.insert_text((480, 125), "01/10/2020", fontsize=9)
    payload = document.tobytes(garbage=4, deflate=True)
    document.close()

    parsed = parse_eurofins_report(payload, source.name)
    assert [participant.full_name for participant in parsed.participants] == [
        "KENNEDY AKWASI APPIAH",
        "DERRICK THOMAS MANU",
        "SECOND PAGE PERSON",
    ]
    assert [participant.source_order for participant in parsed.participants] == [0, 1, 2]


@pytest.mark.parametrize(
    ("case_code", "expected_month"),
    [
        ("PP-EBD-260926-04", 9),
        ("SS-HOM-110926-01", 9),
        ("KSI-KBDO-011026-02", 10),
        ("CD-KBDO-220926-01", 9),
    ],
)
def test_various_case_code_prefixes_parse_successfully(
    project_root: Path,
    case_code: str,
    expected_month: int,
) -> None:
    source = project_root / "CD-EBD-210926-01.pdf"
    document = fitz.open(source)
    page = document[0]
    hits = page.search_for("CD-EBD-210926-01")
    assert len(hits) == 1
    rect = hits[0]
    page.add_redact_annot(rect, fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text((rect.x0, rect.y1 - 1), case_code, fontsize=10, fontname="hebo")
    payload = document.tobytes(garbage=4, deflate=True)
    document.close()

    parsed = parse_eurofins_report(payload, f"{case_code}.pdf")
    assert parsed.case_code == case_code
    assert parsed.collection_date.month == expected_month
    assert not parsed.blocking_errors


def test_multiline_participant_name_with_space_join(project_root: Path) -> None:
    source = project_root / "CD-KBDO-220926-01.pdf"
    document = fitz.open(source)
    page = document[0]
    hits = [h for h in page.search_for("NANA YAW") if 190 <= h.y0 <= 250]
    assert len(hits) == 1
    rect = hits[0]
    page.add_redact_annot(rect, fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text((rect.x0, rect.y1 - 1), "EMMANUEL OBREMPONG", fontsize=9)
    page.insert_text((rect.x0, rect.y1 + 10), "OSHISHI", fontsize=9)
    payload = document.tobytes(garbage=4, deflate=True)
    document.close()

    parsed = parse_eurofins_report(payload, source.name)
    assert len(parsed.participants) == 2
    assert parsed.participants[1].full_name == "EMMANUEL OBREMPONG OSHISHI"


def test_multiline_participant_name_with_hyphen_join(project_root: Path) -> None:
    source = project_root / "CD-KBDO-220926-01.pdf"
    document = fitz.open(source)
    page = document[0]
    hits = [h for h in page.search_for("NANA YAW") if 190 <= h.y0 <= 250]
    assert len(hits) == 1
    rect = hits[0]
    page.add_redact_annot(rect, fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text((rect.x0, rect.y1 - 1), "JUDE NHYIRA ANANE-", fontsize=9)
    page.insert_text((rect.x0, rect.y1 + 10), "FRIMPONG", fontsize=9)
    payload = document.tobytes(garbage=4, deflate=True)
    document.close()

    parsed = parse_eurofins_report(payload, source.name)
    assert len(parsed.participants) == 2
    assert parsed.participants[1].full_name == "JUDE NHYIRA ANANE-FRIMPONG"


def test_multiline_participant_name_father_does_not_leak(project_root: Path) -> None:
    source = project_root / "CD-KBDO-220926-01.pdf"
    document = fitz.open(source)
    page = document[0]
    hits = [h for h in page.search_for("THOMAS KOFI BOAKYE") if 190 <= h.y0 <= 250]
    assert len(hits) == 1
    rect = hits[0]
    page.add_redact_annot(rect, fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text((rect.x0, rect.y1 - 1), "DENIS DJAN", fontsize=9)
    page.insert_text((rect.x0, rect.y1 + 5), "ESSIEN", fontsize=9)
    payload = document.tobytes(garbage=4, deflate=True)
    document.close()

    parsed = parse_eurofins_report(payload, source.name)
    assert len(parsed.participants) == 2
    assert parsed.participants[0].full_name == "DENIS DJAN ESSIEN"
    assert parsed.participants[1].full_name == "NANA YAW"


def test_maternity_report_infers_maternity_and_renders_clean_heading(
    project_root: Path,
) -> None:
    from report_bot.domain import CaseType
    from report_bot.pdf_service import rebrand_pdf

    source = project_root / "CD-EBD-210926-01.pdf"
    document = fitz.open(source)
    page = document[0]
    hit = page.search_for("Alleged Father")[0]
    page.add_redact_annot(hit, fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text((185.0, hit.y1 - 1), "Alleged Mother", fontsize=9)

    # Change "Personal Paternity Test Certificate" to "Personal Maternity Test Certificate"
    thit = page.search_for("Personal Paternity Test Certificate")[0]
    page.add_redact_annot(thit, fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text(
        (thit.x0, thit.y1 - 1),
        "Personal Maternity Test Certificate",
        fontsize=15,
        fontname="hebo",
    )
    payload = document.tobytes(garbage=4, deflate=True)
    document.close()

    parsed = parse_eurofins_report(payload, source.name)
    assert parsed.test_type == "MATERNITY"
    assert not parsed.blocking_errors

    # Rebrand as Personal
    personal_rebranded = rebrand_pdf(
        payload,
        case_type=CaseType.PERSONAL,
        test_type=parsed.test_type,
        logo_path=project_root / "blueprint_logo.png",
    )
    doc_personal = fitz.open(stream=personal_rebranded, filetype="pdf")
    text_personal = doc_personal[0].get_text()
    doc_personal.close()
    assert "Personal Maternity Test Certificate" in text_personal
    assert "Personal Paternity Test Certificate" not in text_personal

    # Rebrand as Legal
    legal_rebranded = rebrand_pdf(
        payload,
        case_type=CaseType.CERTIFIED_LEGAL,
        test_type=parsed.test_type,
        logo_path=project_root / "blueprint_logo.png",
    )
    doc_legal = fitz.open(stream=legal_rebranded, filetype="pdf")
    text_legal = doc_legal[0].get_text()
    doc_legal.close()
    assert "Legal Maternity Test Certificate" in text_legal
    assert "Legal Paternity Test Certificate" not in text_legal
    assert "Personal Maternity Test Certificate" not in text_legal


def test_maternity_with_boilerplate_paternity_does_not_mix(project_root: Path) -> None:
    source = project_root / "CD-EBD-210926-01.pdf"
    document = fitz.open(source)
    page = document[0]
    hit = page.search_for("Alleged Father")[0]
    page.add_redact_annot(hit, fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text((185.0, hit.y1 - 1), "Alleged Mother", fontsize=9)

    # Change title to generic "Personal Test Certificate" while
    # page still contains "Paternity" in footnotes
    thit = page.search_for("Personal Paternity Test Certificate")[0]
    page.add_redact_annot(thit, fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text(
        (thit.x0, thit.y1 - 1),
        "Personal Test Certificate",
        fontsize=15,
        fontname="hebo",
    )
    payload = document.tobytes(garbage=4, deflate=True)
    document.close()

    parsed = parse_eurofins_report(payload, source.name)
    assert parsed.test_type == "MATERNITY"


def test_ychromosome_and_avuncular_test_types_infer_and_rebrand_cleanly(
    project_root: Path,
) -> None:
    from report_bot.domain import CaseType
    from report_bot.pdf_service import rebrand_pdf

    # Test Y-Chromosome
    source = project_root / "CD-EBD-210926-01.pdf"
    document = fitz.open(source)
    page = document[0]
    thit = page.search_for("Personal Paternity Test Certificate")[0]
    page.add_redact_annot(thit, fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text(
        (thit.x0, thit.y1 - 1),
        "Personal Y-Chromosome Test Certificate",
        fontsize=15,
        fontname="hebo",
    )
    payload_y = document.tobytes(garbage=4, deflate=True)
    document.close()

    parsed_y = parse_eurofins_report(payload_y, source.name)
    assert parsed_y.test_type == "Y-CHROMOSOME"

    legal_y = rebrand_pdf(
        payload_y,
        case_type=CaseType.CERTIFIED_LEGAL,
        test_type=parsed_y.test_type,
        logo_path=project_root / "blueprint_logo.png",
    )
    doc_y = fitz.open(stream=legal_y, filetype="pdf")
    assert "Legal Y-Chromosome Test Certificate" in doc_y[0].get_text()
    doc_y.close()

    # Test Avuncular
    document = fitz.open(source)
    page = document[0]
    hit = page.search_for("Alleged Father")[0]
    page.add_redact_annot(hit, fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text((185.0, hit.y1 - 1), "Alleged Uncle", fontsize=9)

    thit = page.search_for("Personal Paternity Test Certificate")[0]
    page.add_redact_annot(thit, fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text(
        (thit.x0, thit.y1 - 1),
        "Personal Avuncular Test Certificate",
        fontsize=15,
        fontname="hebo",
    )
    payload_av = document.tobytes(garbage=4, deflate=True)
    document.close()

    parsed_av = parse_eurofins_report(payload_av, source.name)
    assert parsed_av.test_type == "AVUNCULAR"


def test_cd_fml_partial_dob_report_parses_successfully(project_root: Path) -> None:
    path = project_root / "CD-FML-260926-01.pdf"
    parsed = parse_eurofins_report(path.read_bytes(), path.name)
    assert parsed.case_code == "CD-FML-260926-01"
    assert parsed.test_type == "PATERNITY"
    assert len(parsed.participants) == 2
    assert parsed.participants[0].sample_number == "HID261006_062"
    assert parsed.participants[0].role == "ALLEGED FATHER"
    assert parsed.participants[0].full_name == "SAMUEL ADADE"
    assert parsed.participants[0].date_of_birth == "--/--/1972"
    assert parsed.participants[1].sample_number == "HID261006_063"
    assert parsed.participants[1].role == "CHILD"
    assert parsed.participants[1].full_name == "BEATRICE AYIVOR"
    assert parsed.participants[1].date_of_birth == "22/07/1989"
    assert not parsed.blocking_errors
    assert "One or more participants have no date of birth in the source report" in parsed.warnings


@pytest.mark.parametrize(
    ("raw", "expected_valid", "expected_normalized"),
    [
        ("22/07/1989", True, "22/07/1989"),
        ("22 / 07 / 1989", True, "22/07/1989"),
        ("22.07.1989", True, "22/07/1989"),
        ("22-07-1989", True, "22/07/1989"),
        ("1989-07-22", True, "22/07/1989"),
        ("2/7/1989", True, "02/07/1989"),
        ("-- / -- /1972", True, "--/--/1972"),
        ("--/--/1972", True, "--/--/1972"),
        ("--.--.1972", True, "--/--/1972"),
        ("1972", True, "--/--/1972"),
        ("00/00/1972", True, "--/--/1972"),
        ("-", True, "-"),
        ("--", True, "-"),
        ("---", True, "-"),
        ("-- / -- / ----", True, "-"),
        ("--/--/----", True, "-"),
        ("00/00/0000", True, "-"),
        ("N/A", True, "-"),
        ("UNKNOWN", True, "-"),
        ("DOB UNKNOWN", True, "-"),
        ("", True, "-"),
        (None, True, "-"),
        ("32/01/2000", False, "32/01/2000"),
        ("invalid", False, "INVALID"),
        ("--/--/1850", False, "--/--/1850"),
    ],
)
def test_valid_date_of_birth_and_normalize(
    raw: str | None, expected_valid: bool, expected_normalized: str
) -> None:
    from report_bot.domain import normalize_dob_value, valid_date_of_birth

    assert normalize_dob_value(raw) == expected_normalized
    assert valid_date_of_birth(raw) is expected_valid


def test_all_lab_sources_parse_and_handle_dob_consistently(project_root: Path) -> None:
    from report_bot.domain import LabSource
    from report_bot.parsing import parse_report

    # 1. Eurofins
    eurofins_path = project_root / "CD-FML-260926-01.pdf"
    if eurofins_path.exists():
        parsed = parse_report(LabSource.EUROFINS, eurofins_path.read_bytes(), eurofins_path.name)
        assert parsed.case_code == "CD-FML-260926-01"
        assert len(parsed.participants) == 2
        assert parsed.participants[0].date_of_birth == "--/--/1972"
        assert parsed.participants[1].date_of_birth == "22/07/1989"

    # 2. UFC
    ufc_path = project_root / "CD G34_0412-2.pdf"
    if ufc_path.exists():
        parsed = parse_report(LabSource.UFC, ufc_path.read_bytes(), ufc_path.name)
        assert parsed.case_code == "CD-EBD-260926-03"
        assert len(parsed.participants) == 2
        for p in parsed.participants:
            assert p.date_of_birth == "-"

    # 3. Hong Kong
    hk_path = project_root / "PNP-EBD-250926-01.pdf"
    if hk_path.exists():
        parsed = parse_report(LabSource.HONG_KONG, hk_path.read_bytes(), hk_path.name)
        assert parsed.case_code == "PNP-EBD-250926-01"
        assert len(parsed.participants) == 2
        for p in parsed.participants:
            assert p.date_of_birth == "-"


def test_multisegment_prefix_case_code_preserves_full_code() -> None:
    from datetime import date
    from report_bot.domain import Participant
    from report_bot.parser import _collection_date, _extract_case_code, CASE_RE
    from report_bot.pdf_service import output_filename, password_for_case

    case_code = "CD-BDO-HOM-031026-01"
    text = f"Personal Paternity Test Certificate\nCase reference number: {case_code}"

    extracted = _extract_case_code(text)
    assert extracted == case_code
    assert CASE_RE.fullmatch(extracted)

    col_date = _collection_date(extracted)
    assert col_date == date(2026, 10, 3)

    pwd = password_for_case(extracted, "124bdo")
    assert pwd == "124bdo10"

    p1 = Participant(
        sample_number="H1",
        role="ALLEGED FATHER",
        full_name="KOFI ADADE",
        date_of_birth="01/01/1980",
    )
    p2 = Participant(
        sample_number="H2",
        role="CHILD",
        full_name="AMA ADADE",
        date_of_birth="01/01/2010",
    )

    filename = output_filename(extracted, "PATERNITY", [p1, p2])
    assert filename == "Adade-Blueprint-CD-BDO-HOM-031026-01.pdf"



