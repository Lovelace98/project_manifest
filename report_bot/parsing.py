"""Lab-source-aware parser dispatch."""
from __future__ import annotations

from report_bot.domain import LabSource, ReportData
from report_bot.hongkong_parser import HongKongParseError, parse_hong_kong_report
from report_bot.parser import ReportParseError as EurofinsParseError
from report_bot.parser import parse_eurofins_report
from report_bot.ufc_parser import UFCParseError, parse_ufc_report


class ReportParseError(Exception):
    """Unified parse error for any lab source."""


def parse_report(
    lab_source: LabSource,
    pdf_bytes: bytes,
    filename: str,
    *,
    max_file_bytes: int = 25 * 1024 * 1024,
) -> ReportData:
    """Parse a report PDF using the parser for the specified lab source."""
    if lab_source == LabSource.EUROFINS:
        try:
            return parse_eurofins_report(
                pdf_bytes,
                filename,
                max_file_bytes=max_file_bytes,
            )
        except EurofinsParseError as exc:
            raise ReportParseError(str(exc)) from exc
    elif lab_source == LabSource.UFC:
        try:
            return parse_ufc_report(
                pdf_bytes,
                filename,
                max_file_bytes=max_file_bytes,
            )
        except UFCParseError as exc:
            raise ReportParseError(str(exc)) from exc
    elif lab_source == LabSource.HONG_KONG:
        try:
            return parse_hong_kong_report(
                pdf_bytes,
                filename,
                max_file_bytes=max_file_bytes,
            )
        except HongKongParseError as exc:
            raise ReportParseError(str(exc)) from exc
    else:
        raise ReportParseError(f"Unsupported laboratory source: {lab_source}")
