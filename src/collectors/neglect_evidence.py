"""P1.5-3：被忽视证据链 collectors。

四个独立信号：
- 新闻数 (`news_count_30d`)：东财个股新闻
- 概念板块 (`is_ai_related`)：东财概念板块（AI / 半导体 / 机器人等热门）
- 热点出现频次 (`hot_reason_count_30d`)：同花顺/东财热点（占位实现，待接入）
- 行业相对收益 (`relative_return_60d`)：当前 placeholder，需接入行情接口

最后通过 `compute_neglect_evidence` 聚合成可读证据字符串。

设计原则：
- 每个 collector 独立失败不影响其他信号。
- 缺失信号不参与 neglect 判定（保守）。
- 不改硬过滤，只填 metrics.catalyst + neglect_evidence。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ..collectors._retry import akshare_call
from .base import normalize_code


# AI / 热门概念关键词（用于 is_ai_related 判定）
AI_CONCEPT_KEYWORDS = (
    "人工智能", "AI", "ChatGPT", "大模型",
    "算力", "GPU", "光模块", "数据中心",
    "半导体", "芯片", "集成电路", "封测",
    "机器人", "人形机器人", "减速器",
    "新能源车", "锂电池", "固态电池", "充电桩",
)


class NeglectEvidenceCollector:
    """聚合多个被忽视证据信号。

    用法：
        collector = NeglectEvidenceCollector()
        news_count = collector.get_news_count_30d("600031")
        is_ai = collector.is_ai_related("600031")
        evidence = collector.compute_neglect_evidence(
            reports_count_90d=2, news_count_30d=3,
            is_ai_related=False, hot_reason_count_30d=0,
            relative_return_60d=-0.15,
        )
    """

    def __init__(self):
        self._concept_cache: Optional[pd.DataFrame] = None

    # === 新闻数 ===
    @akshare_call
    def get_news_count_30d(self, code: str) -> int:
        """东财个股新闻近 30 天数量。

        AkShare `stock_news_em(symbol)` 返回近期新闻列表，含发布时间。
        """
        import akshare as ak
        code = normalize_code(code)
        try:
            df = ak.stock_news_em(symbol=code)
        except Exception:
            return 0
        if df is None or len(df) == 0:
            return 0
        # 列名：标题 / 发布时间 / 文章来源 / 新闻链接 / 内容
        date_col = None
        for c in df.columns:
            if "时间" in c or "日期" in c:
                date_col = c
                break
        if date_col is None:
            # 没有时间列，直接返回行数（保守估计）
            return len(df)
        df = df.copy()
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=30)
        return int((df[date_col] >= cutoff).sum())

    # === 概念板块 ===
    @akshare_call
    def _load_all_concepts(self) -> pd.DataFrame:
        """东财概念板块列表（含板块名）。"""
        import akshare as ak
        df = ak.stock_board_concept_name_em()
        if df is None or len(df) == 0:
            return pd.DataFrame()
        return df

    @akshare_call
    def _load_concept_constituents(self, concept_name: str) -> list[str]:
        """东财概念板块成分股（返回 code 列表）。"""
        import akshare as ak
        try:
            df = ak.stock_board_concept_cons_em(symbol=concept_name)
        except Exception:
            return []
        if df is None or len(df) == 0:
            return []
        return df["代码"].astype(str).str.zfill(6).tolist() if "代码" in df.columns else []

    def is_ai_related(self, code: str) -> Optional[bool]:
        """检查股票是否属于 AI/半导体/机器人等热门概念。

        Returns:
            True  → 属于热门概念（不够"被忽视"）
            False → 不属于
            None  → 数据不可用（不参与判定）
        """
        code = normalize_code(code)
        try:
            concepts = self._load_all_concepts()
        except Exception:
            return None
        if concepts.empty:
            return None

        # 找含 AI 关键词的概念
        name_col = None
        for c in concepts.columns:
            if "板块" in c or "名称" in c:
                name_col = c
                break
        if name_col is None:
            return None

        ai_concepts = concepts[
            concepts[name_col].astype(str).str.contains(
                "|".join(AI_CONCEPT_KEYWORDS), regex=True, na=False
            )
        ]
        if ai_concepts.empty:
            return False

        # 检查每个 AI 概念的成分股
        for _, row in ai_concepts.iterrows():
            try:
                cons = self._load_concept_constituents(str(row[name_col]))
                if code in cons:
                    return True
            except Exception:
                continue
        return False

    # === 热点出现频次（占位） ===
    def get_hot_reason_count_30d(self, code: str) -> int:
        """同花顺/东财热点出现频次（占位实现）。

        TODO: 接入 `stock_hot_rank_em` 历史数据。
        当前返回 0，不参与 neglect_evidence 聚合。
        """
        return 0

    # === 行业相对收益（占位） ===
    def get_relative_return_60d(self, code: str) -> Optional[float]:
        """近 60 天相对行业的收益（占位实现）。

        TODO: 需要个股 60 天涨幅 + 行业 60 天涨幅。
        当前返回 None，不参与 neglect_evidence 聚合。
        """
        return None

    # === 聚合 ===
    def compute_neglect_evidence(
        self,
        *,
        reports_count_90d: Optional[int] = None,
        news_count_30d: Optional[int] = None,
        is_ai_related: Optional[bool] = None,
        hot_reason_count_30d: Optional[int] = None,
        relative_return_60d: Optional[float] = None,
        low_report_threshold: int = 3,
        low_news_threshold: int = 5,
    ) -> Optional[str]:
        """把多个被忽视信号聚合成可读证据字符串。

        Returns:
            - None：无任何被忽视证据（或数据缺失）
            - str：人类可读的聚合证据，例如 "近 90 天仅 2 篇研报；近 30 天 3 条新闻；非 AI 概念"
        """
        if all(v is None for v in (
            reports_count_90d, news_count_30d, is_ai_related,
            hot_reason_count_30d, relative_return_60d,
        )):
            return None

        parts: list[str] = []
        if reports_count_90d is not None:
            if reports_count_90d <= low_report_threshold:
                parts.append(f"近 90 天仅 {reports_count_90d} 篇研报")
            else:
                parts.append(f"近 90 天 {reports_count_90d} 篇研报")
        if news_count_30d is not None:
            if news_count_30d <= low_news_threshold:
                parts.append(f"近 30 天仅 {news_count_30d} 条新闻")
            else:
                parts.append(f"近 30 天 {news_count_30d} 条新闻")
        if is_ai_related is False:
            parts.append("非 AI/半导体/机器人概念")
        elif is_ai_related is True:
            parts.append("⚠ 属 AI/半导体/机器人概念（不够被忽视）")
        if hot_reason_count_30d is not None and hot_reason_count_30d > 0:
            parts.append(f"近 30 天上 {hot_reason_count_30d} 次热点")
        if relative_return_60d is not None:
            if relative_return_60d < -0.05:
                parts.append(f"近 60 天跑输行业 {abs(relative_return_60d)*100:.1f}%")
            elif relative_return_60d > 0.05:
                parts.append(f"近 60 天跑赢行业 {relative_return_60d*100:.1f}%")

        return "；".join(parts) if parts else None
