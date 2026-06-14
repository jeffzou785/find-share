"""DuckDB 持久化层。

表设计：
- stocks          : 全市场股票列表（code, name）
- industry_first  : 申万一级行业
- industry_second : 申万二级行业
- stock_industry  : 股票 → 申万一级行业映射（含新浪行业来源 + 实时 PE/PB）
- pe_pb_history   : 单股历史 PE/PB 时间序列
- financials      : 单股财务摘要（长格式：一行一指标）
- disclosures     : 财报披露日历
- overseas_revenue: 年报附注提取的海外收入（Phase 0 输出）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import duckdb
import pandas as pd

from ..config import config


SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS stocks (
    code VARCHAR PRIMARY KEY,
    name VARCHAR,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS industry_first (
    industry_code VARCHAR PRIMARY KEY,
    industry_name VARCHAR,
    constituent_count INTEGER,
    pe_ttm DOUBLE,
    pb DOUBLE,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS industry_second (
    industry_code VARCHAR PRIMARY KEY,
    industry_name VARCHAR,
    parent_industry_name VARCHAR,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stock_industry (
    code VARCHAR PRIMARY KEY,
    name VARCHAR,
    sina_industry VARCHAR,
    sw_first VARCHAR,
    pe_ttm DOUBLE,
    pb DOUBLE,
    total_mktcap_wan DOUBLE,
    float_mktcap_wan DOUBLE,
    turnover_ratio DOUBLE,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pe_pb_history (
    code VARCHAR,
    date DATE,
    close DOUBLE,
    pe_ttm DOUBLE,
    pe_static DOUBLE,
    pb DOUBLE,
    total_mktcap DOUBLE,
    float_mktcap DOUBLE,
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS financials (
    code VARCHAR,
    report_date DATE,
    revenue DOUBLE,
    net_profit DOUBLE,
    net_profit_attr_parent DOUBLE,
    deducted_net_profit DOUBLE,
    gross_margin DOUBLE,
    revenue_yoy DOUBLE,
    net_profit_yoy DOUBLE,
    roe DOUBLE,
    ocf_per_share DOUBLE,
    PRIMARY KEY (code, report_date)
);

CREATE TABLE IF NOT EXISTS disclosures (
    code VARCHAR,
    name VARCHAR,
    period VARCHAR,
    first_schedule DATE,
    actual_date DATE,
    PRIMARY KEY (code, period)
);

CREATE TABLE IF NOT EXISTS overseas_revenue (
    stock_code VARCHAR,
    report_year INTEGER,
    region_name VARCHAR,
    revenue DOUBLE,
    revenue_unit VARCHAR,
    source_page INTEGER,
    raw_text VARCHAR,
    pdf_path VARCHAR,
    PRIMARY KEY (stock_code, report_year, region_name)
);
"""


class DuckDBStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or config.DUCKDB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path))
        self._init_schema()

    def _init_schema(self) -> None:
        for stmt in SCHEMA_DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self.conn.execute(stmt)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # === upsert helpers ===
    def upert_dataframe(self, table: str, df: pd.DataFrame) -> None:
        """简单 upsert：先 delete 再 insert。适合中小批量。"""
        if df.empty:
            return
        # 注册 df 为临时表
        self.conn.register("_tmp", df)
        cols = ", ".join(df.columns)
        # DuckDB 的 INSERT OR REPLACE 语法（PK 必须存在）
        try:
            self.conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) SELECT {cols} FROM _tmp")
        except Exception as e:
            # 兜底：纯 insert
            self.conn.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _tmp")
        self.conn.unregister("_tmp")

    # === stocks ===
    def save_stocks(self, df: pd.DataFrame) -> None:
        df = df.copy()
        df["updated_at"] = pd.Timestamp.now()
        self.upert_dataframe("stocks", df)

    def load_stocks(self) -> pd.DataFrame:
        return self.conn.execute("SELECT code, name FROM stocks").df()

    # === stock_industry ===
    def save_stock_industry(self, df: pd.DataFrame) -> None:
        df = df.copy()
        df["updated_at"] = pd.Timestamp.now()
        # 只保留 stock_industry 表的列
        cols = [
            "code", "name", "sina_industry", "sw_first",
            "pe_ttm", "pb", "total_mktcap_wan", "float_mktcap_wan",
            "turnover_ratio", "updated_at",
        ]
        df = df[[c for c in cols if c in df.columns]]
        self.upert_dataframe("stock_industry", df)

    def load_stock_industry(self, sw_first: list[str] | None = None) -> pd.DataFrame:
        sql = "SELECT * FROM stock_industry"
        if sw_first:
            placeholders = ", ".join([f"'{s}'" for s in sw_first])
            sql += f" WHERE sw_first IN ({placeholders})"
        return self.conn.execute(sql).df()

    # === pe_pb_history ===
    def save_pe_pb_history(self, code: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        df = df.copy()
        df["code"] = code
        df["date"] = pd.to_datetime(df["date"]).dt.date
        cols = [
            "code", "date", "close", "pe_ttm", "pe_static", "pb",
            "total_mktcap", "float_mktcap",
        ]
        df = df[[c for c in cols if c in df.columns]]
        self.upert_dataframe("pe_pb_history", df)

    def load_pe_pb_history(self, code: str) -> pd.DataFrame:
        return self.conn.execute(
            "SELECT * FROM pe_pb_history WHERE code = ? ORDER BY date", [code]
        ).df()

    # === financials ===
    def save_financials(self, code: str, df: pd.DataFrame) -> None:
        if df.empty:
            return
        df = df.copy()
        df["code"] = code
        df["report_date"] = pd.to_datetime(df["report_date"]).dt.date
        cols = [
            "code", "report_date", "revenue", "net_profit",
            "net_profit_attr_parent", "deducted_net_profit",
            "gross_margin", "revenue_yoy", "net_profit_yoy",
            "roe", "ocf_per_share",
        ]
        df = df[[c for c in cols if c in df.columns]]
        self.upert_dataframe("financials", df)

    def load_financials(self, code: str) -> pd.DataFrame:
        return self.conn.execute(
            "SELECT * FROM financials WHERE code = ? ORDER BY report_date", [code]
        ).df()

    # === overseas_revenue ===
    def save_overseas_revenue(self, rows: Iterable[dict]) -> int:
        df = pd.DataFrame(list(rows))
        if df.empty:
            return 0
        cols = [
            "stock_code", "report_year", "region_name", "revenue",
            "revenue_unit", "source_page", "raw_text", "pdf_path",
        ]
        df = df[[c for c in cols if c in df.columns]]
        self.upert_dataframe("overseas_revenue", df)
        return len(df)

    def load_overseas_revenue(self) -> pd.DataFrame:
        return self.conn.execute("SELECT * FROM overseas_revenue").df()

    # === disclosures ===
    def save_disclosures(self, df: pd.DataFrame, period: str) -> None:
        df = df.copy()
        df["period"] = period
        cols = ["code", "name", "period", "first_schedule", "actual_date"]
        df = df[[c for c in cols if c in df.columns]]
        self.upert_dataframe("disclosures", df)

    def load_disclosures(self, period: str) -> pd.DataFrame:
        return self.conn.execute(
            "SELECT * FROM disclosures WHERE period = ?", [period]
        ).df()
