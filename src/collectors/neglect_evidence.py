"""P1.5-3：被忽视证据链 collectors。

四个独立信号：
- 新闻数 (`news_count_30d`)：东财个股新闻
- 概念板块 (`is_ai_related`)：东财概念板块（AI / 半导体 / 机器人等热门）
- 热点出现频次 (`hot_reason_count_30d`)：同花顺每日热点，按日期缓存
- 相对收益 (`relative_return_60d`)：个股近 60 交易日相对可配置基准收益

最后通过 `compute_neglect_evidence` 聚合成可读证据字符串。

设计原则：
- 每个 collector 独立失败不影响其他信号。
- 缺失信号不参与 neglect 判定（保守）。
- 不改硬过滤，只填 metrics.catalyst + neglect_evidence。
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
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


def _extract_codes_from_value(value) -> set[str]:
    """从接口字段值中提取 6 位 A 股代码。"""
    codes: set[str] = set()
    if value is None:
        return codes
    for match in re.findall(r"(?<!\d)(\d{6})(?!\d)", str(value)):
        codes.add(match)
    return codes


def _extract_stock_codes_from_obj(obj) -> set[str]:
    """递归解析热点接口返回体中的股票代码。"""
    codes: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_text = str(key).lower()
            if "code" in key_text or "代码" in key_text:
                codes.update(_extract_codes_from_value(value))
            else:
                codes.update(_extract_stock_codes_from_obj(value))
    elif isinstance(obj, list):
        for item in obj:
            codes.update(_extract_stock_codes_from_obj(item))
    elif isinstance(obj, (str, int, float)):
        codes.update(_extract_codes_from_value(obj))
    return codes


def _parse_json_payload(text: str):
    """兼容 JSON / JSONP / GBK 解码后的热点文本。"""
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return None
    return None


def _pick_column(df: pd.DataFrame, candidates: tuple[str, ...]) -> Optional[str]:
    for col in candidates:
        if col in df.columns:
            return col
    for col in df.columns:
        col_text = str(col).lower()
        if any(c.lower() in col_text for c in candidates):
            return col
    return None


def period_return(df: pd.DataFrame, lookback_days: int = 60) -> Optional[float]:
    """按最近 N 条交易记录计算区间收益。"""
    if df is None or df.empty:
        return None
    date_col = _pick_column(df, ("日期", "date"))
    close_col = _pick_column(df, ("收盘", "close"))
    if close_col is None:
        return None
    data = df.copy()
    if date_col is not None:
        data[date_col] = pd.to_datetime(data[date_col], errors="coerce")
        data = data.dropna(subset=[date_col]).sort_values(date_col)
    data[close_col] = pd.to_numeric(data[close_col], errors="coerce")
    data = data.dropna(subset=[close_col])
    if len(data) < 2:
        return None
    window = data.tail(lookback_days + 1)
    start = float(window.iloc[0][close_col])
    end = float(window.iloc[-1][close_col])
    if start <= 0:
        return None
    return end / start - 1.0


def relative_return(
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    lookback_days: int = 60,
) -> Optional[float]:
    """个股收益 - 基准收益。"""
    stock_ret = period_return(stock_df, lookback_days=lookback_days)
    benchmark_ret = period_return(benchmark_df, lookback_days=lookback_days)
    if stock_ret is None or benchmark_ret is None:
        return None
    return stock_ret - benchmark_ret


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

    def __init__(
        self,
        *,
        hot_lookback_days: int = 30,
        relative_return_days: int = 60,
        benchmark_symbol: str = "sh000300",
        request_timeout: int = 8,
    ):
        self._concept_cache: Optional[pd.DataFrame] = None
        self.hot_lookback_days = hot_lookback_days
        self.relative_return_days = relative_return_days
        self.benchmark_symbol = benchmark_symbol
        self.request_timeout = request_timeout
        self._hot_codes_cache: dict[str, set[str]] = {}

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

    # === 热点出现频次 ===
    def get_hot_reason_count_30d(self, code: str) -> int:
        """同花顺每日热点近 30 天出现频次。

        接口失败时返回 0，保持被忽视证据链的保守可用性。
        """
        code = normalize_code(code)
        today = date.today()
        count = 0
        for offset in range(self.hot_lookback_days):
            day = today - timedelta(days=offset)
            try:
                codes = self._load_hot_codes_for_date(day.strftime("%Y%m%d"))
            except Exception:
                codes = set()
            if code in codes:
                count += 1
        return count

    def _load_hot_codes_for_date(self, date_str: str) -> set[str]:
        if date_str in self._hot_codes_cache:
            return self._hot_codes_cache[date_str]

        import requests

        url = (
            "http://zx.10jqka.com.cn/event/api/getharden/"
            f"date/{date_str}/orderby/date/orderway/desc/charset/GBK/"
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
            ),
            "Referer": "http://zx.10jqka.com.cn/",
        }
        resp = requests.get(url, headers=headers, timeout=self.request_timeout)
        text = resp.content.decode("gbk", errors="ignore")
        payload = _parse_json_payload(text)
        codes = _extract_stock_codes_from_obj(payload) if payload is not None else set()
        self._hot_codes_cache[date_str] = codes
        return codes

    # === 相对收益 ===
    def get_relative_return_60d(self, code: str) -> Optional[float]:
        """近 60 交易日相对基准收益。

        默认基准为沪深 300（`sh000300`）。未来接入行业指数时，只需要替换
        benchmark_symbol 或新增行业映射，不需要改策略层。
        """
        code = normalize_code(code)
        try:
            stock_df = self._load_stock_hist(code)
            benchmark_df = self._load_benchmark_hist(self.benchmark_symbol)
            return relative_return(
                stock_df, benchmark_df,
                lookback_days=self.relative_return_days,
            )
        except Exception:
            return None

    @akshare_call
    def _load_stock_hist(self, code: str) -> pd.DataFrame:
        import akshare as ak

        end_date = date.today()
        start_date = end_date - timedelta(days=self.relative_return_days * 3)
        return ak.stock_zh_a_hist(
            symbol=normalize_code(code),
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="qfq",
        )

    @akshare_call
    def _load_benchmark_hist(self, symbol: str) -> pd.DataFrame:
        import akshare as ak

        df = ak.stock_zh_index_daily(symbol=symbol)
        if df is None or df.empty:
            return pd.DataFrame()
        date_col = _pick_column(df, ("日期", "date"))
        if date_col is not None:
            cutoff = pd.Timestamp.now() - pd.Timedelta(days=self.relative_return_days * 3)
            df = df.copy()
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            df = df[df[date_col] >= cutoff]
        return df

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
                parts.append(f"近 60 天跑输基准 {abs(relative_return_60d)*100:.1f}%")
            elif relative_return_60d > 0.05:
                parts.append(f"近 60 天跑赢基准 {relative_return_60d*100:.1f}%")

        return "；".join(parts) if parts else None
