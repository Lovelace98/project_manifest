from datetime import date
from pathlib import Path

import pytest

from report_bot.domain import LabSource
from report_bot.parsing import parse_report
from report_bot.ufc_parser import UFCParseError, parse_ufc_report

BASE_DIR = Path(__file__).resolve().parent.parent


def test_parse_ufc_ychromosome_cd_g34_0412_2():
    fixture_path = BASE_DIR / "CD G34_0412-2.pdf"
    if not fixture_path.exists():
        pytest.skip("CD G34_0412-2.pdf not found")
    pdf_bytes = fixture_path.read_bytes()
    report = parse_ufc_report(pdf_bytes, "CD G34_0412-2.pdf")

    assert report.case_code == "CD-EBD-260926-03"
    assert report.collection_date == date(2026, 9, 26)
    assert report.report_date == date(2026, 10, 6)
    assert report.test_type == "Y-CHROMOSOME"
    assert report.confidence == 1.0
    assert not report.blocking_errors
    assert "One or more participants have no date of birth in the source report" in report.warnings
    assert not any("unrecognized" in w for w in report.warnings)
    assert not any("No child role" in w for w in report.warnings)

    assert len(report.participants) == 2
    p1, p2 = report.participants
    assert p1.sample_number == "G34_0412-2Y1"
    assert p1.full_name == "JOSEPH AGYAPONG"
    assert p1.role == "ALLEGED MALE RELATIVE"

    assert p2.sample_number == "G34_0412-2Y2"
    assert p2.full_name == "MOSES AGYAPONG"
    assert p2.role == "ALLEGED MALE RELATIVE"


def test_parse_ufc_paternity_g34_0408_1():
    fixture_path = BASE_DIR / "G34_0408-1.pdf"
    if not fixture_path.exists():
        pytest.skip("G34_0408-1.pdf not found")
    pdf_bytes = fixture_path.read_bytes()
    report = parse_ufc_report(pdf_bytes, "G34_0408-1.pdf")

    assert report.case_code == "SS-MDL-210926-01"
    assert report.collection_date == date(2026, 9, 21)
    assert report.report_date == date(2026, 10, 2)
    assert report.test_type == "PATERNITY"
    assert report.confidence == 1.0
    assert not report.blocking_errors
    assert "One or more participants have no date of birth in the source report" in report.warnings

    assert len(report.participants) == 2
    father, child = report.participants
    assert father.sample_number == "G34_0408-1AF"
    assert father.full_name == "SAMUEL OKATA"
    assert father.role == "ALLEGED FATHER"
    assert father.date_of_birth == "-"

    assert child.sample_number == "G34_0408-1C"
    assert child.full_name == "CHALTON OWUSU"
    assert child.role == "CHILD"
    assert child.date_of_birth == "-"


def test_parse_ufc_paternity_g34_0411_1():
    fixture_path = BASE_DIR / "G34_0411-1.pdf"
    if not fixture_path.exists():
        pytest.skip("G34_0411-1.pdf not found")
    pdf_bytes = fixture_path.read_bytes()
    report = parse_ufc_report(pdf_bytes, "G34_0411-1.pdf")

    assert report.case_code == "SS-HOM-220926-01"
    assert report.collection_date == date(2026, 9, 22)
    assert report.report_date == date(2026, 10, 2)
    assert report.test_type == "PATERNITY"
    assert len(report.participants) == 2
    assert report.participants[0].full_name == "ABRAHAM IDUN AMOAH"
    assert report.participants[1].full_name == "JOSHUA AMOAH"


def test_parse_ufc_siblingship_g34_0403_2():
    fixture_path = BASE_DIR / "G34_0403-2.pdf"
    if not fixture_path.exists():
        pytest.skip("G34_0403-2.pdf not found")
    pdf_bytes = fixture_path.read_bytes()
    report = parse_ufc_report(pdf_bytes, "G34_0403-2.pdf")

    assert report.case_code == "CD-KBDO-170926-03"
    assert report.collection_date == date(2026, 9, 17)
    assert report.report_date == date(2026, 10, 2)
    assert report.test_type == "SIBLINGSHIP"
    assert len(report.participants) == 2
    assert report.participants[0].full_name == "GORDON MAXWELL DERY"
    assert report.participants[0].role == "ALLEGED SIBLING"
    assert report.participants[1].full_name == "EMMANUEL BONSU DERI"
    assert report.participants[1].role == "ALLEGED SIBLING"


def test_parsing_dispatch_ufc():
    fixture_path = BASE_DIR / "CD G34_0412-2.pdf"
    if not fixture_path.exists():
        pytest.skip("CD G34_0412-2.pdf not found")
    pdf_bytes = fixture_path.read_bytes()
    report = parse_report(LabSource.UFC, pdf_bytes, "CD G34_0412-2.pdf")
    assert report.case_code == "CD-EBD-260926-03"
    assert report.test_type == "Y-CHROMOSOME"


def test_parse_ufc_invalid_pdf():
    with pytest.raises(UFCParseError):
        parse_ufc_report(b"not a pdf", "test.pdf")
