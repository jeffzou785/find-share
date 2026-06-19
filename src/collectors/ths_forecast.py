"""同花顺一致预期 EPS（basic.10jqka.com.cn）。

用于和东财研报的预测做交叉验证（同花顺覆盖机构数通常少于东财，
但口径独立，可以用来发现东财异常值）。

接口返回 32 个 HTML 表，我们只取 Table 0（EPS 一致预期汇总）
和 Table 1（净利润一致预期汇总）入库。
"""
from __future__ import annotations

from io import StringIO

import pandas as pd
import requests

from ._em_throttle import UA


THS_FORECAST_URL = "https://basic.10jqka.com.cn/new/{code}/worth.html"


class ThsForecastSource:
    """同花顺一致预期数据源。"""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def get_eps_forecast(self, code: str) -> pd.DataFrame:
        """拉取一致预期 EPS 汇总（合并 Table 0 EPS + Table 1 净利润）。

        返回长格式 DataFrame：
        code, forecast_year, broker_count, eps_min, eps_mean, eps_max, net_profit_mean
        net_profit_mean 单位为「亿元」（已从原始字符串如 "861.83亿" 解析）。
        """
        code = str(code).strip().zfill(6)
        url = THS_FORECAST_URL.format(code=code)
        headers = {
            "User-Agent": UA,
            "Referer": "https://basic.10jqka.com.cn/",
        }
        try:
            r = requests.get(url, headers=headers, timeout=self.timeout)
        except requests.RequestException as e:
            raise RuntimeError(f"同花顺请求失败 {code}: {e}") from e
        if r.status_code != 200:
            raise RuntimeError(f"同花顺 {code} HTTP {r.status_code}")

        r.encoding = "gbk"
        try:
            dfs = pd.read_html(StringIO(r.text))
        except ValueError as e:
            raise RuntimeError(f"同花顺 {code} 解析 HTML 失败: {e}") from e

        if not dfs:
            raise RuntimeError(f"同花顺 {code} 没解析到任何表")

        eps_df = self._find_summary_table(dfs, "每股收益")
        np_df = self._find_summary_table(dfs, "净利润")

        if eps_df is None:
            raise RuntimeError(f"同花顺 {code} 未找到 EPS 汇总表")

        # 规整 EPS 表
        eps_df = eps_df.rename(
            columns={
                "年度": "forecast_year",
                "预测机构数": "broker_count",
                "最小值": "eps_min",
                "均值": "eps_mean",
                "最大值": "eps_max",
            }
        )
        eps_df["forecast_year"] = pd.to_numeric(eps_df["forecast_year"], errors="coerce").astype("Int64")
        for col in ("broker_count", "eps_min", "eps_mean", "eps_max"):
            eps_df[col] = pd.to_numeric(eps_df[col], errors="coerce")
        eps_df = eps_df[["forecast_year", "broker_count", "eps_min", "eps_mean", "eps_max"]]

        # 合并净利润均值
        if np_df is not None:
            np_df = np_df.rename(columns={"年度": "forecast_year", "均值": "_np_mean"})
            np_df["forecast_year"] = pd.to_numeric(np_df["forecast_year"], errors="coerce").astype("Int64")
            np_df["net_profit_mean"] = np_df["_np_mean"].apply(_parse_yi_value)
            eps_df = eps_df.merge(
                np_df[["forecast_year", "net_profit_mean"]],
                on="forecast_year",
                how="left",
            )
        else:
            eps_df["net_profit_mean"] = None

        eps_df.insert(0, "code", code)
        return eps_df.reset_index(drop=True)

    @staticmethod
    def _find_summary_table(
        dfs: list[pd.DataFrame], keyword: str
    ) -> pd.DataFrame | None:
        """在多个 HTML 表中找 EPS/净利润 汇总表（年度/预测机构数/最小值/均值/最大值）。"""
        for df in dfs:
            cols = list(df.columns)
            if "年度" in cols and "均值" in cols and "预测机构数" in cols:
                # 区分 EPS 表 vs 净利润表：通过看均值列的数值量级
                # EPS 表均值通常 < 200，净利润均值通常 > 100（亿）
                try:
                    sample = pd.to_numeric(df["均值"].head(3), errors="coerce").dropna()
                    if sample.empty:
                        continue
                    avg_value = sample.mean()
                    if keyword == "每股收益" and avg_value < 1000:
                        return df
                    if keyword == "净利润" and avg_value >= 100:
                        return df
                except Exception:
                    continue
        return None


def _parse_yi_value(val) -> float | None:
    """把 "861.83亿" 解析为 861.83（亿元）。"""
    if val is None or val == "":
        return None
    s = str(val).strip()
    # 移除可能的非数字字符
    for unit in ("亿元", "亿", "万元", "万", "元"):
        if s.endswith(unit):
            s = s[: -len(unit)].strip()
            try:
                v = float(s.replace(",", ""))
                if unit == "万元":
                    return v / 1e4
                if unit == "万":
                    return v / 1e4
                if unit == "元":
                    return v / 1e8
                return v  # 亿
            except ValueError:
                return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None
