"""F10 text fallback for overseas revenue extraction.

This module is intentionally split into a pure text parser and an optional
mootdx fetcher. The parser is fast and unit-testable; the fetcher is only used
as a best-effort fallback when PDF table extraction fails.
"""
from __future__ import annotations

import re
import socket
from pathlib import Path
from typing import Iterable, Optional

from .annual_report_parser import (
    DOMESTIC_KEYWORDS,
    OVERSEAS_KEYWORDS,
    TOTAL_ROW_KEYWORDS,
    ParseResult,
    OverseasRevenueRecord,
    UNIT_TO_YUAN,
    _match_overseas_region,
    _parse_revenue_from_row,
    _validate_revenue_yuan,
)
from .base import normalize_code


F10_CATEGORIES = ("财务分析", "公司概况")

_TDX_SERVERS = [
    ("119.97.185.59", 7709),
    ("124.70.133.119", 7709),
    ("116.205.183.150", 7709),
    ("123.60.73.44", 7709),
    ("116.205.163.254", 7709),
    ("121.36.225.169", 7709),
    ("123.60.70.228", 7709),
    ("124.71.9.153", 7709),
    ("110.41.147.114", 7709),
    ("124.71.187.122", 7709),
]
_TDX_CLIENT = None
_TDX_CLIENT_ERROR: Optional[str] = None


def _probe(ip: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def _tdx_client():
    """Create a mootdx client using explicit servers to avoid BESTIP empty config."""
    global _TDX_CLIENT, _TDX_CLIENT_ERROR
    if _TDX_CLIENT is not None:
        return _TDX_CLIENT
    if _TDX_CLIENT_ERROR is not None:
        raise RuntimeError(_TDX_CLIENT_ERROR)

    try:
        from mootdx.quotes import Quotes
    except ModuleNotFoundError as exc:
        _TDX_CLIENT_ERROR = "mootdx 未安装，无法使用 F10 fallback"
        raise RuntimeError(_TDX_CLIENT_ERROR) from exc

    for ip, port in _TDX_SERVERS:
        if _probe(ip, port):
            _TDX_CLIENT = Quotes.factory(market="std", server=(ip, port))
            return _TDX_CLIENT
    try:
        _TDX_CLIENT = Quotes.factory(market="std", bestip=True)
        return _TDX_CLIENT
    except Exception:
        pass
    try:
        _TDX_CLIENT = Quotes.factory(market="std")
        return _TDX_CLIENT
    except Exception as exc:
        _TDX_CLIENT_ERROR = f"mootdx F10 服务器不可达: {exc}"
        raise RuntimeError(_TDX_CLIENT_ERROR) from exc


def fetch_mootdx_f10_text(
    code: str,
    *,
    categories: Iterable[str] = F10_CATEGORIES,
) -> str:
    """Fetch and concatenate selected mootdx F10 categories."""
    client = _tdx_client()
    code = normalize_code(code)
    chunks: list[str] = []
    for category in categories:
        try:
            text = client.F10(symbol=code, name=category)
        except Exception:
            continue
        if text:
            chunks.append(f"=== {category} ===\n{text}")
    return "\n".join(chunks)


def _unit_from_text(text: str) -> Optional[str]:
    if "百万元" in text:
        return "百万"
    for unit in ("亿元", "万元", "千元", "元"):
        if unit in text:
            return unit
    return None


def _line_year(line: str) -> Optional[int]:
    match = re.search(r"(20\d{2})(?:[-/年.])", line)
    return int(match.group(1)) if match else None


def parse_f10_overseas_records(
    text: str,
    *,
    stock_code: str = "",
    report_year: int | None = None,
) -> list[OverseasRevenueRecord]:
    """Parse overseas revenue rows from F10 text.

    F10 text is not a stable table API, so this parser is conservative:
    it only keeps lines with overseas keywords and a parsable money amount.
    """
    if not text:
        return []

    default_unit = _unit_from_text(text) or "元"
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
    has_domestic = any(any(kw in line for kw in DOMESTIC_KEYWORDS) for line in lines)
    has_overseas = any(any(kw in line for kw in OVERSEAS_KEYWORDS) for line in lines)
    page_confidence = "high" if has_domestic and has_overseas else "medium"

    records: list[OverseasRevenueRecord] = []
    active_year: int | None = None
    active_unit = default_unit
    for line in lines:
        line_unit = _unit_from_text(line)
        if line_unit:
            active_unit = line_unit
        year = _line_year(line)
        if year:
            active_year = year
        if report_year is not None and active_year is not None and active_year != report_year:
            continue

        region = _match_overseas_region(line)
        if not region:
            continue
        if "分行业" in line or "分产品" in line or "分客户" in line:
            continue

        revenue, unit = _parse_revenue_from_row(line, active_unit)
        if revenue is None:
            continue

        revenue_yuan = _validate_revenue_yuan(revenue * UNIT_TO_YUAN.get(unit, 1.0))
        is_total_row = any(kw in line for kw in TOTAL_ROW_KEYWORDS)
        confidence = "low" if is_total_row else page_confidence
        if active_year is None and report_year is not None:
            confidence = "medium" if confidence == "high" else confidence

        records.append(OverseasRevenueRecord(
            stock_code=normalize_code(stock_code) if stock_code else "",
            report_period=f"{report_year}年报" if report_year else "",
            region_name=region,
            revenue=revenue,
            revenue_unit=unit,
            revenue_yuan=revenue_yuan,
            source_page=-1,
            raw_text=line[:160],
            is_total_row=is_total_row,
            confidence=confidence,
        ))
    return records


def parse_f10_overseas_revenue(
    text: str,
    *,
    stock_code: str = "",
    report_year: int | None = None,
    source: str = "mootdx_f10",
) -> ParseResult:
    """Parse F10 text into the same ParseResult shape as the PDF parser."""
    code = normalize_code(stock_code) if stock_code else ""
    result = ParseResult(
        stock_code=code,
        pdf_path=f"{source}:{code}:{report_year or ''}",
        success=False,
        notes=["f10_fallback"],
    )
    records = parse_f10_overseas_records(
        text, stock_code=code, report_year=report_year,
    )
    if not records:
        result.error = "F10 未提取到境外收入数据"
        return result
    result.records = records
    result.success = True
    result.parse_warnings.append("f10_fallback_used")
    return result


def fetch_mootdx_f10_overseas_revenue(
    code: str,
    *,
    report_year: int | None = None,
) -> ParseResult:
    """Best-effort overseas revenue fallback via mootdx F10."""
    code = normalize_code(code)
    try:
        text = fetch_mootdx_f10_text(code)
    except Exception as exc:
        return ParseResult(
            stock_code=code,
            pdf_path=f"mootdx_f10:{code}:{report_year or ''}",
            success=False,
            error=f"mootdx_f10_error:{type(exc).__name__}: {exc}",
            notes=["f10_fallback"],
        )
    return parse_f10_overseas_revenue(
        text, stock_code=code, report_year=report_year, source="mootdx_f10",
    )


__all__ = [
    "fetch_mootdx_f10_overseas_revenue",
    "fetch_mootdx_f10_text",
    "parse_f10_overseas_records",
    "parse_f10_overseas_revenue",
]
