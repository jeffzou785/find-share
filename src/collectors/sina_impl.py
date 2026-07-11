"""新浪财报三表数据源（利润表 / 资产负债表 / 现金流量表）。

接口：https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022
返回字段为中文科目，本模块做中英映射并规整为长格式。

与 akshare_impl.get_financial_abstract 互补：
- akshare 给的是 11 字段摘要，覆盖关键字段
- sina 给的是完整三表细粒度（每表 30~60 个科目）
- 同一字段（如 deducted_net_profit）以 sina 为主路径，akshare 作交叉验证
"""
from __future__ import annotations

import functools
import hashlib
import pickle
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import requests
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import config


class SinaTransientError(Exception):
    """新浪接口偶发错误（限流、超时、字段漂移）。"""


UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

SINA_FINANCE_URL = (
    "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
)


# 中文科目 → 英文字段映射。
# 未命中映射的科目会保留 item_cn 原文落库（item_en 留空），便于后续扩展。
ITEM_CN_MAP: dict[str, str] = {
    # ── 利润表 ──
    "营业总收入": "revenue",
    "其中：营业收入": "operating_revenue",
    "营业收入": "operating_revenue",
    "营业总成本": "total_operating_cost",
    "其中：营业成本": "operating_cost",
    "营业成本": "operating_cost",
    "营业税金及附加": "taxes_and_surcharges",
    "销售费用": "selling_expense",
    "管理费用": "admin_expense",
    "财务费用": "financial_expense",
    "研发费用": "rd_expense",
    "资产减值损失": "asset_impairment_loss",
    "信用减值损失": "credit_impairment_loss",
    "公允价值变动收益": "fv_change_gain",
    "投资收益": "investment_income",
    "资产处置收益": "asset_disposal_income",
    "其他收益": "other_income",
    "营业利润": "operating_profit",
    "营业外收入": "non_operating_income",
    "营业外支出": "non_operating_expense",
    "利润总额": "total_profit",
    "所得税费用": "income_tax_expense",
    "净利润": "net_profit",
    "归属于母公司股东的净利润": "net_profit_attr_parent",
    "归属于母公司所有者的净利润": "net_profit_attr_parent",
    "扣除非经常性损益后的净利润": "deducted_net_profit",
    "基本每股收益": "eps_basic",
    "稀释每股收益": "eps_diluted",
    "销售毛利率": "gross_margin",
    "销售净利率": "net_margin",
    "利息费用": "interest_expense",
    "利息收入": "interest_income",
    # ── 资产负债表 ──
    "货币资金": "cash",
    "交易性金融资产": "trading_financial_assets",
    "应收票据": "notes_receivable",
    "应收账款": "accounts_receivable",
    "预付款项": "prepayments",
    "其他应收款": "other_receivables",
    "存货": "inventory",
    "其他流动资产": "other_current_assets",
    "流动资产合计": "total_current_assets",
    "可供出售金融资产": "available_for_sale_financial_assets",
    "持有至到期投资": "held_to_maturity_investments",
    "长期应收款": "long_term_receivables",
    "长期股权投资": "long_term_equity_investment",
    "投资性房地产": "investment_property",
    "固定资产": "fixed_assets",
    "在建工程": "construction_in_progress",
    "使用权资产": "right_of_use_assets",
    "无形资产": "intangible_assets",
    "商誉": "goodwill",
    "长期待摊费用": "long_term_prepaid_expenses",
    "递延所得税资产": "deferred_tax_assets",
    "其他非流动资产": "other_non_current_assets",
    "一年内到期的非流动资产": "non_current_assets_due_within_one_year",
    "非流动资产合计": "total_non_current_assets",
    "资产总计": "total_assets",
    "短期借款": "short_term_loan",
    "交易性金融负债": "trading_financial_liabilities",
    "应付票据": "notes_payable",
    "应付账款": "accounts_payable",
    "预收款项": "advances_from_customers",
    "应付职工薪酬": "salaries_payable",
    "应交税费": "taxes_payable",
    "其他应付款": "other_payables",
    "合同负债": "contract_liabilities",
    "一年内到期的非流动负债": "non_current_liabilities_due_within_one_year",
    "流动负债合计": "total_current_liabilities",
    "长期借款": "long_term_loan",
    "应付债券": "bonds_payable",
    "长期应付款": "long_term_payables",
    "租赁负债": "lease_liabilities",
    "预计负债": "estimated_liabilities",
    "递延所得税负债": "deferred_tax_liabilities",
    "其他非流动负债": "other_non_current_liabilities",
    "非流动负债合计": "total_non_current_liabilities",
    "负债合计": "total_liabilities",
    "实收资本（或股本）": "share_capital",
    "实收资本": "share_capital",
    "资本公积": "capital_reserve",
    "减：库存股": "treasury_stock",
    "其他综合收益": "other_comprehensive_income",
    "盈余公积": "surplus_reserve",
    "未分配利润": "retained_earnings",
    "归属于母公司股东权益合计": "equity_attr_parent",
    "少数股东权益": "minority_interest",
    "所有者权益（或股东权益）合计": "total_equity",
    "所有者权益合计": "total_equity",
    "负债和所有者权益总计": "total_liabilities_and_equity",
    # ── 现金流量表 ──
    "销售商品、提供劳务收到的现金": "cash_from_sales",
    "收到的税费返还": "tax_refund_received",
    "收到其他与经营活动有关的现金": "other_operating_cash_inflow",
    "经营活动现金流入小计": "ocf_inflow_subtotal",
    "购买商品、接受劳务支付的现金": "cash_for_purchases",
    "支付给职工以及为职工支付的现金": "cash_for_employees",
    "支付的各项税费": "taxes_paid",
    "支付其他与经营活动有关的现金": "other_operating_cash_outflow",
    "经营活动现金流出小计": "ocf_outflow_subtotal",
    "经营活动产生的现金流量净额": "ocf_net",
    "收回投资收到的现金": "cash_from_investment_recovered",
    "取得投资收益收到的现金": "cash_from_investment_income",
    "处置固定资产、无形资产和其他长期资产所收回的现金净额": "cash_from_asset_disposal",
    "投资活动现金流入小计": "icf_inflow_subtotal",
    "购建固定资产、无形资产和其他长期资产支付的现金": "cash_for_asset_purchase",
    "投资活动现金流出小计": "icf_outflow_subtotal",
    "投资活动产生的现金流量净额": "icf_net",
    "吸收投资收到的现金": "cash_from_capital_raised",
    "取得借款收到的现金": "cash_from_borrowing",
    "筹资活动现金流入小计": "fcf_inflow_subtotal",
    "偿还债务支付的现金": "cash_for_debt_repayment",
    "分配股利、利润或偿付利息所支付的现金": "cash_for_dividends_and_interest",
    "筹资活动现金流出小计": "fcf_outflow_subtotal",
    "筹资活动产生的现金流量净额": "fcf_net",
    "现金及现金等价物净增加额": "net_cash_increase",
    "期末现金及现金等价物余额": "cash_end",
}

STATEMENT_TYPES = {"lrb": "利润表", "fzb": "资产负债表", "llb": "现金流量表"}


def _build_retry_decorator():
    return retry(
        stop=stop_after_attempt(config.AKSHARE_MAX_RETRIES),
        wait=wait_exponential(
            multiplier=config.AKSHARE_RETRY_BASE_DELAY,
            min=config.AKSHARE_RETRY_BASE_DELAY,
            max=config.AKSHARE_RETRY_BASE_DELAY * 10,
        ),
        retry=retry_if_exception_type(SinaTransientError),
        reraise=True,
    )


def _sina_cached(func: Callable) -> Callable:
    """新浪调用的缓存装饰器（pickle，24h TTL）。键基于函数名 + 参数。"""

    @functools.wraps(func)
    def wrapper(*args: Any, force_refresh: bool = False, **kwargs: Any) -> pd.DataFrame:
        cache_key = _make_cache_key(func.__name__, args, kwargs)
        cache_path = config.CACHE_DIR / "sina" / f"{cache_key}.pkl"

        if not force_refresh and cache_path.exists():
            age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
            if age_hours < 24:
                try:
                    with cache_path.open("rb") as f:
                        return pickle.load(f)
                except Exception:
                    cache_path.unlink(missing_ok=True)

        retried = _build_retry_decorator()(func)
        try:
            df = retried(*args, **kwargs)
        except RetryError as e:
            raise SinaTransientError(
                f"{func.__name__} 重试 {config.AKSHARE_MAX_RETRIES} 次后仍失败: {e}"
            ) from e

        if df is None:
            raise SinaTransientError(f"{func.__name__} 返回 None（接口异常或字段漂移）")

        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with cache_path.open("wb") as f:
                pickle.dump(df, f)
        except Exception:
            pass

        return df

    return wrapper


def _make_cache_key(func_name: str, args: tuple, kwargs: dict) -> str:
    key_str = f"{func_name}|{args!r}|{sorted(kwargs.items())!r}"
    return hashlib.md5(key_str.encode("utf-8")).hexdigest()[:16]


def _to_float(val: Any) -> float | None:
    """新浪返回的数值可能是字符串（含千分位逗号、单位、空值）。"""
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "").replace("%", "").replace("元", "")
    if s in ("-", "--", "null", "NULL", "nan", "NaN"):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_value_with_yoy(raw_value: Any, raw_yoy: Any) -> tuple[float | None, float | None]:
    """新浪 item_value 是字符串数值；item_tongbi 是百分比同比。"""
    return _to_float(raw_value), _to_float(raw_yoy)


class SinaFinancialSource:
    """新浪财报三表数据源。"""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    @_sina_cached
    def get_income_statement(self, code: str, num: int = 8) -> pd.DataFrame:
        """利润表（lrb）。返回长格式 DataFrame：
        code, report_date, statement_type, item_cn, item_en, value, value_yoy
        """
        return self._fetch(code, "lrb", num)

    @_sina_cached
    def get_balance_sheet(self, code: str, num: int = 8) -> pd.DataFrame:
        """资产负债表（fzb）。"""
        return self._fetch(code, "fzb", num)

    @_sina_cached
    def get_cashflow(self, code: str, num: int = 8) -> pd.DataFrame:
        """现金流量表（llb）。"""
        return self._fetch(code, "llb", num)

    def get_all_statements(self, code: str, num: int = 8) -> pd.DataFrame:
        """三表合并拉取，返回单一长格式 DataFrame。"""
        frames = [
            self.get_income_statement(code, num),
            self.get_balance_sheet(code, num),
            self.get_cashflow(code, num),
        ]
        return pd.concat([f for f in frames if not f.empty], ignore_index=True)

    def _fetch(self, code: str, report_type: str, num: int) -> pd.DataFrame:
        if report_type not in STATEMENT_TYPES:
            raise ValueError(f"未知 report_type={report_type}，必须是 lrb/fzb/llb")

        code = str(code).strip().zfill(6)
        prefix = "sh" if code.startswith("6") else ("bj" if code.startswith(("8", "4")) else "sz")
        paper_code = f"{prefix}{code}"

        params = {
            "paperCode": paper_code,
            "source": report_type,
            "type": "0",
            "page": "1",
            "num": str(num),
        }
        try:
            r = requests.get(
                SINA_FINANCE_URL, params=params, headers={"User-Agent": UA}, timeout=self.timeout
            )
        except requests.RequestException as e:
            raise SinaTransientError(f"sina 请求失败 {code} {report_type}: {e}") from e

        if r.status_code != 200:
            raise SinaTransientError(f"sina {code} {report_type} HTTP {r.status_code}")

        try:
            payload = r.json()
        except ValueError as e:
            raise SinaTransientError(f"sina {code} {report_type} 返回非 JSON: {e}") from e

        report_list = payload.get("result", {}).get("data", {}).get("report_list", {}) or {}
        if not report_list:
            raise SinaTransientError(
                f"sina {code} {report_type} report_list 为空（可能未披露或代码错误）"
            )

        rows: list[dict] = []
        unmatched: set[str] = set()
        for period in sorted(report_list.keys(), reverse=True)[:num]:
            obj = report_list[period]
            report_date = pd.to_datetime(
                f"{period[:4]}-{period[4:6]}-{period[6:8]}", errors="coerce"
            )
            for item in obj.get("data", []) or []:
                title = item.get("item_title", "")
                raw_value = item.get("item_value")
                if not title or raw_value is None:
                    continue
                value, yoy = _parse_value_with_yoy(raw_value, item.get("item_tongbi"))
                item_en = ITEM_CN_MAP.get(title, "")
                if not item_en:
                    unmatched.add(title)
                rows.append(
                    {
                        "code": code,
                        "report_date": report_date,
                        "statement_type": report_type,
                        "item_cn": title,
                        "item_en": item_en,
                        "value": value,
                        "value_yoy": yoy,
                    }
                )

        if unmatched:
            # 字段漂移监控：未命中映射的科目打印一次告警（不影响主流程）
            print(
                f"[sina] WARN {code} {report_type} 有 {len(unmatched)} 个科目未映射: "
                f"{sorted(unmatched)[:5]}{'...' if len(unmatched) > 5 else ''}"
            )

        return pd.DataFrame(rows)
