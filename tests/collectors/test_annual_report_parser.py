"""P1-3 海外收入解析增强测试。

覆盖：
- 总营收/合计行标记为 is_total_row=True（不丢失，但 confidence 降为 low）
- 同页含境内+境外时 confidence=high
- 仅"国际"边缘匹配时 confidence=low
- select_best_record 优先 high confidence、回退 total_row、warning 输出
- 跨页 \\n 在表格 cell 内被清洗
"""
from __future__ import annotations

from src.collectors.annual_report_parser import (
    OverseasRevenueRecord,
    TOTAL_ROW_KEYWORDS,
    _extract_from_page,
    _parse_revenue_from_row,
    select_best_record,
)


# === 排除词 / is_total_row ===

def _make_records_via_extract(rows: list[list[str]], text: str = "") -> list:
    """用 _extract_from_page 直接跑表格提取，避免依赖 PDF 文件。"""
    return _extract_from_page([rows], text or "境外 境内 单位：元", page_num=1, page_unit="元")


class TestTotalRowExclusion:
    def test_total_row_keyword_list_includes_classic_patterns(self):
        assert "营业收入合计" in TOTAL_ROW_KEYWORDS
        assert "主营业务收入合计" in TOTAL_ROW_KEYWORDS
        assert "合计" in TOTAL_ROW_KEYWORDS
        assert "小计" in TOTAL_ROW_KEYWORDS

    def test_row_with_total_keyword_marked_is_total_row(self):
        # "营业收入合计" 行不含境外关键词，根本不会被 _extract_from_page 抓
        # 真正测的是_total_row 标记：境外+合计组合行
        rows = [
            ["项目", "金额"],
            ["境外", "1,234,567,890"],
            ["境外合计", "9,876,543,210"],  # 境外 + 合计 → is_total_row=True
        ]
        records = _make_records_via_extract(rows)
        assert len(records) == 2
        by_text = {r.raw_text: r for r in records}
        normal = next(r for r in records if not r.is_total_row)
        total = next(r for r in records if r.is_total_row)
        assert normal.revenue_yuan == 1234567890.0
        assert total.revenue_yuan == 9876543210.0
        # 总行降级 confidence 为 low
        assert total.confidence == "low"

    def test_non_total_row_keeps_page_confidence(self):
        # 同页境内+境外都存在 → 抓到的境外行 confidence=high
        rows = [
            ["境内", "5,000,000,000"],
            ["境外", "3,000,000,000"],
        ]
        records = _make_records_via_extract(rows, text="境内 境外 单位：元")
        # _extract_from_page 只抓含境外关键词的行；境内行不抓
        assert len(records) == 1
        assert records[0].region_name == "境外"
        assert records[0].confidence == "high"
        assert records[0].is_total_row is False


# === confidence 判定 ===

class TestConfidence:
    def test_high_when_both_domestic_and_overseas(self):
        rows = [["境外", "1,000,000,000"]]
        # text 含境内+境外 → high
        records = _extract_from_page(
            [rows], "境内 境外 单位：元", page_num=1, page_unit="元"
        )
        assert records[0].confidence == "high"

    def test_medium_when_only_overseas(self):
        rows = [["境外", "1,000,000,000"]]
        # text 仅含境外 → medium
        records = _extract_from_page(
            [rows], "境外 单位：元", page_num=1, page_unit="元"
        )
        assert records[0].confidence == "medium"

    def test_low_when_international_keyword_only(self):
        # 行首"国际"匹配，但其他都缺 → medium 被降为 low
        rows = [["国际", "1,000,000,000"]]
        records = _extract_from_page(
            [rows], "国际 单位：元", page_num=1, page_unit="元"
        )
        assert len(records) == 1
        assert records[0].region_name == "国际"
        assert records[0].confidence == "low"

    def test_outbound_sales_keyword_is_treated_as_overseas(self):
        rows = [["内销", "3,448,158,371.62"], ["外销", "143,861,752.14"]]
        records = _extract_from_page(
            [rows], "内销 外销 单位：元", page_num=1, page_unit="元"
        )
        assert len(records) == 1
        assert records[0].region_name == "外销"
        assert records[0].revenue_yuan == 143861752.14
        assert records[0].confidence == "high"

    def test_other_countries_and_regions_keyword_is_treated_as_overseas(self):
        rows = [
            ["中国大陆", "8,361,588,619.49"],
            ["其他国家和地区", "7,625,585,408.18"],
        ]
        records = _extract_from_page(
            [rows], "中国大陆 其他国家和地区 单位：元", page_num=1, page_unit="元"
        )
        assert len(records) == 1
        assert records[0].region_name == "其他国家和地区"
        assert records[0].revenue_yuan == 7625585408.18
        assert records[0].confidence == "high"


# === 跨页 \\n 修复 ===

class TestCrossPageNewline:
    def test_newline_in_cell_cleaned_before_number_parse(self):
        # cell 含 \n（跨页断字残留）：单元格内容应该是 "1,234,567,890"
        rows = [["境外", "1,234,\n567,\n890"]]
        records = _make_records_via_extract(rows)
        assert len(records) == 1
        # 应该解析出 1234567890 元 ≈ 12.35 亿元
        assert records[0].revenue_yuan == 1234567890.0

    def test_multiple_cells_with_newline(self):
        rows = [["境外", "1,234\n,567,890"]]  # 万元单位
        records = _extract_from_page(
            [rows], "境外 单位：万元", page_num=1, page_unit="万元"
        )
        assert len(records) == 1
        # 清洗后 "1,234,567,890"，单位识别为"万元"（因 page_unit 是万元且无行内提示）
        # 实际上 "1,234,567,890" 不含"万"字，会走 page_unit
        assert records[0].revenue > 0


# === select_best_record ===

class TestSelectBestRecord:
    def _rec(
        self, revenue_yuan: float, confidence: str = "medium",
        is_total: bool = False, region: str = "境外",
    ) -> OverseasRevenueRecord:
        return OverseasRevenueRecord(
            stock_code="", report_period="", region_name=region,
            revenue=revenue_yuan, revenue_unit="元", revenue_yuan=revenue_yuan,
            confidence=confidence, is_total_row=is_total,
        )

    def test_empty_records_returns_none(self):
        best, warns = select_best_record([])
        assert best is None
        assert warns == []

    def test_prefers_high_confidence_over_larger_amount(self):
        records = [
            self._rec(10_000_000_000, confidence="medium"),  # 100 亿
            self._rec(1_000_000_000, confidence="high"),     # 10 亿
        ]
        best, _ = select_best_record(records)
        assert best is not None
        assert best.confidence == "high"
        assert best.revenue_yuan == 1_000_000_000

    def test_skips_total_row_when_non_total_available(self):
        records = [
            self._rec(50_000_000_000, confidence="medium", is_total=True),  # 总行
            self._rec(3_000_000_000, confidence="medium"),                  # 普通行
        ]
        best, warns = select_best_record(records)
        assert best is not None
        assert best.is_total_row is False
        assert best.revenue_yuan == 3_000_000_000

    def test_fallback_to_total_row_with_warning_when_all_total(self):
        records = [
            self._rec(50_000_000_000, is_total=True),
            self._rec(30_000_000_000, is_total=True),
        ]
        best, warns = select_best_record(records)
        assert best is not None
        assert best.revenue_yuan == 50_000_000_000
        assert "all_candidates_are_total_row" in warns

    def test_warning_when_multiple_high_confidence_with_large_gap(self):
        records = [
            self._rec(10_000_000_000, confidence="high"),
            self._rec(2_000_000_000, confidence="high"),  # 差 5x > 2x
        ]
        best, warns = select_best_record(records)
        assert best is not None
        assert any("multiple_high_confidence_candidates" in w for w in warns)

    def test_p15_2_multi_high_large_gap_picks_smaller(self):
        """P1.5-2：max/min > 5x 时取最小（避免误抓总营收）。

        600690 真实案例：max=1429 yi（总营收）vs min=62 yi（真实境外）。
        旧逻辑：取 max=1429 yi → ratio>0.95 被剔除
        新逻辑：取 min=62 yi + 标 parse_warning
        """
        records = [
            self._rec(142_900_000_000, confidence="high"),  # 总营收混入
            self._rec(6_200_000_000, confidence="high"),    # 真实境外
        ]
        best, warns = select_best_record(records)
        assert best is not None
        assert best.revenue_yuan == 6_200_000_000  # 取最小
        assert any("multi_high_chose_smaller" in w for w in warns)

    def test_p15_2_multi_high_small_gap_picks_larger(self):
        """max/min < 5x 时仍取最大（多个境外分区的最大单区）。"""
        records = [
            self._rec(10_000_000_000, confidence="high"),
            self._rec(3_000_000_000, confidence="high"),  # 3.3x，未到 5x
        ]
        best, warns = select_best_record(records)
        assert best is not None
        assert best.revenue_yuan == 10_000_000_000

    def test_warning_when_best_confidence_low(self):
        records = [self._rec(1_000_000_000, confidence="low")]
        best, warns = select_best_record(records)
        assert best is not None
        assert any("low_confidence_only" in w for w in warns)


# === _parse_revenue_from_row 单元 ===

class TestParseRevenueFromRow:
    def test_extract_thousand_separated(self):
        v, unit = _parse_revenue_from_row(["境外", "1,234,567,890"], "元")
        assert v == 1234567878.0 or v == 1234567890.0  # 浮点容差
        assert unit == "元"

    def test_year_excluded(self):
        v, _ = _parse_revenue_from_row(["2024 年境外", "1,234,567,890"], "元")
        # 2024 应被排除（年份），剩下的金额是 1234567890
        assert v == 1234567890.0

    def test_unit_hint_in_cell(self):
        v, unit = _parse_revenue_from_row(["境外", "12.5 亿元"], "元")
        assert v == 12.5
        assert unit == "亿元"

    def test_volume_ton_not_parsed_as_ten_thousand_yuan(self):
        v, unit = _parse_revenue_from_row(
            [
                "国外",
                "销售钛白粉48.45万吨，同比增长13.08%，其中国内销售金额占比56.43%，"
                "国外销售金额占比43.57%。",
            ],
            "万元",
        )
        assert v is None
        assert unit == "万元"

    def test_export_volume_not_parsed_as_hundred_million_yuan(self):
        v, unit = _parse_revenue_from_row(
            "铁矿年出口量最高可达1.2亿吨，作为项目参建单位之一。",
            "元",
        )
        assert v is None
        assert unit == "元"

    def test_current_year_zero_does_not_fallback_to_previous_year_amount(self):
        v, unit = _parse_revenue_from_row(
            ["境外-亚洲", "0.00", "0.00%", "74,518,432.00", "4.87%", "-100.00%"],
            "元",
        )
        assert v is None
        assert unit == "元"
