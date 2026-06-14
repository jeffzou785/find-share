"""年报附注解析器（Phase 0 POC 核心）。

从上市公司年报 PDF 中提取"分地区营业收入"附注里的境外/海外收入。
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber

warnings.filterwarnings("ignore", category=UserWarning, module="pdfplumber")


@dataclass
class OverseasRevenueRecord:
    """单条海外收入记录。"""
    stock_code: str
    report_period: str  # 如 "2024年报"
    region_name: str  # 如 "境外" / "国外" / "出口"
    revenue: Optional[float]  # 原始金额（按 source 单位）
    revenue_unit: str  # "元" / "千元" / "万元" / "亿元"
    revenue_yuan: Optional[float] = None  # 折算成元
    currency: str = "CNY"
    source_page: int = -1
    raw_text: str = ""  # 用于调试的原文片段


@dataclass
class ParseResult:
    """年报解析结果。"""
    stock_code: str
    pdf_path: str
    success: bool
    records: list[OverseasRevenueRecord] = field(default_factory=list)
    error: str = ""
    notes: list[str] = field(default_factory=list)


# 关键词匹配：分地区营业收入附注的标题
SECTION_TITLES = [
    "分地区",
    "分区域",
    "按地区",
    "按区域",
    "分地区情况",
    "营业收入分地区",
    "营业收入分区域",
    "主营业务分地区",
    "主营业务分区域",
    "主营业务分地区情况",
]

# 境外关键词（按优先级排序：精确词在前）
OVERSEAS_KEYWORDS = [
    "境外",
    "国外",
    "海外",
    "出口",
    "国际",  # 三一重工用"国际"作为境外行
    "其他(境外)",
    "国外（境外）",
]

# 国内关键词（用于校验：如果表格里同时有"境内"和"境外"，可信度高）
DOMESTIC_KEYWORDS = ["境内", "国内", "华北", "华东", "华南", "华中", "西南", "西北", "东北"]

# 单位换算到元
UNIT_TO_YUAN: dict[str, float] = {
    "元": 1.0,
    "千元": 1_000.0,
    "万元": 10_000.0,
    "百万": 1_000_000.0,
    "亿元": 100_000_000.0,
}


def parse_annual_report(pdf_path: str | Path, stock_code: str = "") -> ParseResult:
    """解析单份年报 PDF，提取海外收入记录。"""
    pdf_path = str(pdf_path)
    if not stock_code:
        stock_code = Path(pdf_path).stem.split("_")[0]

    result = ParseResult(stock_code=stock_code, pdf_path=pdf_path, success=False)

    try:
        with pdfplumber.open(pdf_path) as pdf:
            overseas_pages = _find_overseas_section_pages(pdf)
            if not overseas_pages:
                result.error = "未找到分地区营业收入附注"
                return result

            result.notes.append(f"分地区附注出现在第 {overseas_pages[:5]} 页")

            for page_num in overseas_pages:
                page = pdf.pages[page_num - 1]
                tables = page.extract_tables()
                text = page.extract_text() or ""
                # 优先从页面文本识别单位
                page_unit = _detect_page_unit(text)
                records = _extract_from_page(tables, text, page_num, page_unit)
                if records:
                    result.records.extend(records)

            # 跨页去重 + 同页去重
            # 同金额（revenue_yuan）+ 同地区关键词的记录视为同一笔
            result.records = _dedupe_records(result.records)

            result.success = len(result.records) > 0
            if not result.success:
                result.error = f"在第 {overseas_pages} 页找到附注但未提取到境外数据"

    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"

    return result


def _find_overseas_section_pages(pdf) -> list[int]:
    """快速扫描全 PDF，定位"分地区"附注所在的页码。

    年报中"分地区"标题和表格可能跨页，所以命中后也加入下一页。
    """
    title_pages: list[int] = []
    total = len(pdf.pages)
    scan_order = list(range(total - 1, -1, -1))
    for i in scan_order:
        try:
            text = pdf.pages[i].extract_text() or ""
        except Exception:
            continue
        if any(t in text for t in SECTION_TITLES):
            if "营业收入" in text or "主营业务" in text:
                title_pages.append(i + 1)  # 1-indexed
                if len(title_pages) >= 5:
                    break

    if not title_pages:
        return []

    # 加入相邻页（标题在 N 页，表格可能在 N 或 N+1 页）
    pages_set: set[int] = set()
    for p in title_pages:
        pages_set.add(p)
        if p + 1 <= total:
            pages_set.add(p + 1)
        if p - 1 >= 1:
            pages_set.add(p - 1)
    return sorted(pages_set)


def _detect_page_unit(text: str) -> str:
    """从页面文本识别单位（如 "单位：千元" "单位：万元"）。

    年报里"单位："标识可能出现在多个表格中（营业收入、成本分析等），
    不同表单位可能不一样。这里返回首个匹配，可能不准。
    建议配合 _validate_revenue_yuan 做合理性校验。
    """
    # 找形如 "单位：xxx" 或 "单位:xxx" 的提示
    m = re.search(r"单位[:：]\s*([亿万百千]+元|元|千元|万元|百万|亿元)", text)
    if m:
        return m.group(1)
    return "元"


# 海外收入合理性上限：单个公司海外收入不超过 5 万亿元（中石油级才到这个量级）
REVENUE_SANITY_MAX_YUAN = 5e12


def _validate_revenue_yuan(revenue_yuan: float) -> float:
    """对换算后的金额做合理性校验，自动修正明显的单位错误。"""
    if revenue_yuan <= 0:
        return revenue_yuan
    # 超过 5 万亿，可能单位多乘了"万"
    if revenue_yuan > REVENUE_SANITY_MAX_YUAN:
        return revenue_yuan / 1e4
    return revenue_yuan


def _extract_from_page(
    tables: list, text: str, page_num: int, page_unit: str = "元"
) -> list[OverseasRevenueRecord]:
    """从单页的表格和文本中提取境外收入记录。"""
    records: list[OverseasRevenueRecord] = []

    # 优先：从表格提取（表格结构化最好）
    for table in tables:
        for row in table:
            row_text = " ".join(str(c) for c in row if c)
            region = _match_overseas_region(row_text)
            if region:
                # 校验：同一表格行不应是"分行业/分产品"标题
                if "分行业" in row_text or "分产品" in row_text or "分客户" in row_text:
                    continue
                revenue, unit = _parse_revenue_from_row(row, page_unit)
                if revenue is not None:
                    revenue_yuan = _validate_revenue_yuan(revenue * UNIT_TO_YUAN.get(unit, 1.0))
                    records.append(
                        OverseasRevenueRecord(
                            stock_code="",
                            report_period="",
                            region_name=region,
                            revenue=revenue,
                            revenue_unit=unit,
                            revenue_yuan=revenue_yuan,
                            source_page=page_num,
                            raw_text=row_text[:120],
                        )
                    )

    # 退路：从纯文本按行提取（pdfplumber 未识别出表格时）
    if not records:
        for line in text.split("\n"):
            region = _match_overseas_region(line)
            if region:
                if "分行业" in line or "分产品" in line or "分客户" in line:
                    continue
                revenue, unit = _parse_revenue_from_row(line, page_unit)
                if revenue is not None:
                    revenue_yuan = _validate_revenue_yuan(revenue * UNIT_TO_YUAN.get(unit, 1.0))
                    records.append(
                        OverseasRevenueRecord(
                            stock_code="",
                            report_period="",
                            region_name=region,
                            revenue=revenue,
                            revenue_unit=unit,
                            revenue_yuan=revenue_yuan,
                            source_page=page_num,
                            raw_text=line[:120],
                        )
                    )

    return records


def _match_overseas_region(text: str) -> Optional[str]:
    """判断文本是否包含境外关键词，返回匹配到的关键词。

    要求"境外/国外/海外/出口"作为独立词出现（前后不是其他汉字），
    避免"国际业务部"这种误匹配。
    """
    # "国际" 单独匹配太宽泛，要求是行首或独立词
    # 用正则匹配：行首或非汉字字符 + 国际 + 行尾或非汉字字符
    for kw in OVERSEAS_KEYWORDS:
        if kw in ("国际",):
            # 国际单独匹配容易误伤，要求是行首
            if re.search(rf"^国际(?![部业务内])", text.strip()):
                return kw
        elif kw in text:
            return kw
    return None


def _parse_revenue_from_row(row_text_or_list, page_unit: str = "元") -> tuple[Optional[float], str]:
    """从行文本中提取金额。返回 (金额, 单位)。

    单位优先级：行内 "亿元/万元" 提示 > 页面单位 > 默认"元"
    """
    if isinstance(row_text_or_list, list):
        text = " ".join(str(c) for c in row_text_or_list if c)
    else:
        text = str(row_text_or_list)

    # 单位识别
    if "亿元" in text or "亿" in text:
        unit = "亿元"
    elif "百万元" in text or "百万" in text:
        unit = "百万"
    elif "千万元" in text or "万元" in text or "万" in text:
        unit = "万元"
    elif "千元" in text or "千元)" in text:
        unit = "千元"
    else:
        # 没行内单位提示时，用页面单位
        unit = page_unit

    # 数字提取：找金额（含千分位逗号或小数）
    # 排除：年份（19xx/20xx）、百分比、小整数
    candidates = re.findall(
        r"(?<!\d)(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{4,})(?!\d|%|\.\d)",
        text,
    )
    for c in candidates:
        if re.fullmatch(r"(19|20)\d{2}", c):
            continue
        try:
            v = float(c.replace(",", ""))
        except ValueError:
            continue
        # 过滤掉极小数（行号、百分比残留）
        if v < 100:
            continue
        return v, unit

    return None, unit


def _dedupe_records(records: list[OverseasRevenueRecord]) -> list[OverseasRevenueRecord]:
    """跨页去重：相同金额(元) + 相同地区的视为同一笔，保留首个。"""
    seen: set[tuple] = set()
    unique: list[OverseasRevenueRecord] = []
    for r in records:
        # 用四舍五入到元的金额做 key（容忍浮点误差）
        amount_key = round(r.revenue_yuan or 0)
        key = (r.region_name, amount_key)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def summarize_results(results: list[ParseResult]) -> dict:
    """汇总 POC 测试结果。"""
    total = len(results)
    success = sum(1 for r in results if r.success)
    return {
        "total": total,
        "success": success,
        "failed": total - success,
        "success_rate": success / total * 100 if total else 0,
        "failures": [
            {"stock_code": r.stock_code, "error": r.error} for r in results if not r.success
        ],
    }
