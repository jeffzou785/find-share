"""年报附注解析器（Phase 0 POC 核心）。

从上市公司年报 PDF 中提取"分地区营业收入"附注里的境外/海外收入。

P1-3 增强：
- 排除"合计/小计/总计/营业收入合计/主营业务收入合计"等总营收行
- 同页同时出现境内+境外时置信度 high
- 跨页表格单元格内的 \\n 断字修复
- ParseResult.parse_warnings 记录单位识别疑点
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
    is_total_row: bool = False  # True 表示该行疑似总营收/合计行（P1-3）
    confidence: str = "medium"  # high / medium / low（P1-3）


@dataclass
class ParseResult:
    """年报解析结果。"""
    stock_code: str
    pdf_path: str
    success: bool
    records: list[OverseasRevenueRecord] = field(default_factory=list)
    error: str = ""
    notes: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)  # P1-3


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
    # P1.5-2：区域级境外关键词（如部分年报不分"境外"，直接列地区）
    "美洲", "北美", "南美",
    "欧洲", "欧盟",
    "亚洲", "东南亚", "东亚",
    "非洲",
    "大洋洲",
    "日韩", "中日韩",
]

# 国内关键词（用于校验：如果表格里同时有"境内"和"境外"，可信度高）
DOMESTIC_KEYWORDS = ["境内", "国内", "华北", "华东", "华南", "华中", "西南", "西北", "东北"]

# 总营收/合计行排除词（P1-3）：行命中任一词且同时含境外关键词时，标记 is_total_row
# 注：单独的"合计"匹配过宽（如"境外小计"），但实际年报里"境外合计"罕见；
# 配合"分行业/分产品/分客户"已有的硬过滤，加上下面这些组合词后能挡住绝大多数误抓
TOTAL_ROW_KEYWORDS = [
    "营业收入合计",
    "主营业务收入合计",
    "营业总收入",
    "总收入",
    "合计",
    "小计",
    "总计",
]

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
    """从单页的表格和文本中提取境外收入记录。

    返回的记录都带 confidence：
    - high  : 该页同时出现境内+境外关键词（强证据是分地区表）
    - medium: 仅境外关键词
    - low   : 仅靠"国际"等边缘词匹配
    """
    records: list[OverseasRevenueRecord] = []

    # 该页是否同时含境内+境外（决定 confidence）
    has_domestic = any(kw in text for kw in DOMESTIC_KEYWORDS)
    has_overseas_section = any(kw in text for kw in OVERSEAS_KEYWORDS)
    page_confidence = "high" if has_domestic and has_overseas_section else "medium"

    def _make_record(
        region: str, revenue: float, unit: str,
        raw_text: str, page_num: int, page_confidence: str,
        is_total_row: bool,
    ) -> OverseasRevenueRecord:
        revenue_yuan = _validate_revenue_yuan(revenue * UNIT_TO_YUAN.get(unit, 1.0))
        # 总营收行降级 confidence 为 low
        confidence = "low" if is_total_row else page_confidence
        # "国际" 这种边缘匹配也降级
        if region == "国际":
            confidence = "low" if confidence == "medium" else confidence
        return OverseasRevenueRecord(
            stock_code="",
            report_period="",
            region_name=region,
            revenue=revenue,
            revenue_unit=unit,
            revenue_yuan=revenue_yuan,
            source_page=page_num,
            raw_text=raw_text[:160],
            is_total_row=is_total_row,
            confidence=confidence,
        )

    # 优先：从表格提取（表格结构化最好）
    for table in tables:
        for row in table:
            # 跨页断字修复：单元格内的 \n 替换为空，再拼接
            cleaned_cells = [
                ("" if c is None else str(c).replace("\n", "").replace("\r", ""))
                for c in row
            ]
            row_text = " ".join(c for c in cleaned_cells if c)
            region = _match_overseas_region(row_text)
            if not region:
                continue
            # 排除：分行业/分产品/分客户 标题行
            if "分行业" in row_text or "分产品" in row_text or "分客户" in row_text:
                continue
            # P1-3：总营收/合计行标记（仍抓取，但 is_total_row=True；上游选择时可剔除）
            is_total_row = any(kw in row_text for kw in TOTAL_ROW_KEYWORDS)
            revenue, unit = _parse_revenue_from_row(cleaned_cells, page_unit)
            if revenue is not None:
                records.append(_make_record(
                    region, revenue, unit, row_text, page_num,
                    page_confidence, is_total_row,
                ))

    # 退路：从纯文本按行提取（pdfplumber 未识别出表格时）
    if not records:
        for line in text.split("\n"):
            line_clean = line.replace("\n", "").replace("\r", "")
            region = _match_overseas_region(line_clean)
            if not region:
                continue
            if "分行业" in line_clean or "分产品" in line_clean or "分客户" in line_clean:
                continue
            is_total_row = any(kw in line_clean for kw in TOTAL_ROW_KEYWORDS)
            revenue, unit = _parse_revenue_from_row(line_clean, page_unit)
            if revenue is not None:
                records.append(_make_record(
                    region, revenue, unit, line_clean, page_num,
                    page_confidence, is_total_row,
                ))

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
    # 最小金额阈值随单位调整：亿元单位下 0.01 亿（1 百万元）也算合法
    min_value_by_unit = {
        "元": 100.0,
        "千元": 1.0,        # 1 千元 = 1000 元
        "万元": 1.0,        # 1 万元 = 10000 元
        "百万": 1.0,        # 1 百万 = 100 万元
        "亿元": 0.01,       # 0.01 亿元 = 100 万元
    }
    min_value = min_value_by_unit.get(unit, 100.0)
    for c in candidates:
        if re.fullmatch(r"(19|20)\d{2}", c):
            continue
        try:
            v = float(c.replace(",", ""))
        except ValueError:
            continue
        # 过滤掉极小数（行号、百分比残留）
        if v < min_value:
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


# 置信度排序：high > medium > low
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}

# P1.5-2：多 high 候选金额差异倍数阈值（max/min > 此值 → 误抓总营收风险高）
MULTI_HIGH_AMOUNT_RATIO = 5.0


def select_best_record(
    records: list[OverseasRevenueRecord],
) -> tuple[Optional[OverseasRevenueRecord], list[str]]:
    """P1-3 + P1.5-2：从候选记录里选最佳记录。

    选择规则：
    1. 优先 confidence=high 的非 total_row 记录
    2. 若全为 total_row，回退取最大金额（标 warning）
    3. 同 confidence 下：
       - 仅一条 → 直接取
       - 多条且金额差异 ≤ 5x → 取最大（可能是分地区境外行汇总的最大单区）
       - 多条且金额差异 > 5x → P1.5-2 改进：取**最小**值
         （max 很可能是误抓的总营收，min 更可能是真实的境外行）

    Returns:
        (best_record, warnings)
        best_record 可能为 None（候选为空时）
        warnings 是选择过程中产生的疑点（多年交叉校验和 ratio 校验由 import 层做）
    """
    if not records:
        return None, []

    warnings: list[str] = []
    non_total = [r for r in records if not r.is_total_row]
    pool = non_total if non_total else records
    if not non_total:
        warnings.append("all_candidates_are_total_row")

    high_conf = [r for r in pool if r.confidence == "high"]
    other_conf = [r for r in pool if r.confidence != "high"]

    # P1.5-2：多 high 候选金额差异大时，取最小（避免误抓总营收）
    if len(high_conf) >= 2:
        amounts = sorted([r.revenue_yuan or 0 for r in high_conf], reverse=True)
        ratio_max_min = amounts[0] / amounts[-1] if amounts[-1] > 0 else float("inf")
        if ratio_max_min > MULTI_HIGH_AMOUNT_RATIO:
            # 取最小的高置信度候选
            best = min(high_conf, key=lambda r: r.revenue_yuan or 0)
            warnings.append(
                f"multi_high_chose_smaller:max={amounts[0]/1e8:.2f}yi "
                f"min={amounts[-1]/1e8:.2f}yi ratio={ratio_max_min:.1f}x"
            )
        else:
            # 金额差异小：取最大（原行为）
            best = max(high_conf, key=lambda r: r.revenue_yuan or 0)
            if amounts[0] > 0 and amounts[-1] / amounts[0] < 0.5:
                warnings.append(
                    f"multiple_high_confidence_candidates:"
                    f"max={amounts[0]/1e8:.2f}yi min={amounts[-1]/1e8:.2f}yi"
                )
    elif high_conf:
        best = high_conf[0]
    else:
        # 没有 high conf：取其他中置信度最高 + 金额最大
        pool_sorted = sorted(
            other_conf,
            key=lambda r: (_CONFIDENCE_RANK.get(r.confidence, 0), r.revenue_yuan or 0),
            reverse=True,
        )
        best = pool_sorted[0] if pool_sorted else None

    if best is None:
        return None, warnings

    # 最佳记录置信度低 → warning
    if best.confidence == "low":
        warnings.append(f"low_confidence_only:best={best.confidence}")

    return best, warnings
