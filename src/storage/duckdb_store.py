"""DuckDB 持久化层。

表设计：
- stocks          : 全市场股票列表（code, name）
- industry_first  : 申万一级行业
- industry_second : 申万二级行业
- stock_industry  : 股票 → 申万一级行业映射（含新浪行业来源 + 实时 PE/PB）
- pe_pb_history   : 单股历史 PE/PB 时间序列
- financials      : 单股财务摘要（长格式：一行一指标）
- financials_full : 新浪三表细粒度（长格式：code + report_date + statement_type + item）
- broker_reports  : 券商研报列表（含评级 + 一致预期 EPS）
- eps_forecast_consensus: 全市场一致预期 EPS 汇总（同花顺）
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

CREATE TABLE IF NOT EXISTS financials_full (
    code VARCHAR,
    report_date DATE,
    statement_type VARCHAR,
    item_cn VARCHAR,
    item_en VARCHAR,
    value DOUBLE,
    value_yoy DOUBLE,
    updated_at TIMESTAMP,
    PRIMARY KEY (code, report_date, statement_type, item_cn)
);

CREATE TABLE IF NOT EXISTS broker_reports (
    code VARCHAR,
    stock_name VARCHAR,
    title VARCHAR,
    broker VARCHAR,
    broker_code VARCHAR,
    rating VARCHAR,
    rating_prev VARCHAR,
    rating_idx DOUBLE,
    eps_forecast_y1 DOUBLE,
    eps_forecast_y2 DOUBLE,
    eps_forecast_y3 DOUBLE,
    pe_forecast_y1 DOUBLE,
    pe_forecast_y2 DOUBLE,
    pe_forecast_y3 DOUBLE,
    industry VARCHAR,
    publish_date DATE,
    research_date DATE,
    info_code VARCHAR,
    report_id VARCHAR PRIMARY KEY,
    pdf_path VARCHAR,
    ingested_to_rag BOOLEAN,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS eps_forecast_consensus (
    code VARCHAR,
    forecast_year INTEGER,
    broker_count INTEGER,
    eps_min DOUBLE,
    eps_mean DOUBLE,
    eps_max DOUBLE,
    net_profit_mean DOUBLE,
    updated_at TIMESTAMP,
    PRIMARY KEY (code, forecast_year)
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

    # === financials_full（新浪三表细粒度） ===
    def save_financials_full(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        df = df.copy()
        df["report_date"] = pd.to_datetime(df["report_date"]).dt.date
        df["updated_at"] = pd.Timestamp.now()
        cols = [
            "code", "report_date", "statement_type",
            "item_cn", "item_en", "value", "value_yoy", "updated_at",
        ]
        df = df[[c for c in cols if c in df.columns]]
        self.upert_dataframe("financials_full", df)
        return len(df)

    def load_financials_full(
        self,
        code: str,
        statement_type: str | None = None,
        item_en: str | None = None,
    ) -> pd.DataFrame:
        sql = "SELECT * FROM financials_full WHERE code = ?"
        params: list = [code]
        if statement_type:
            sql += " AND statement_type = ?"
            params.append(statement_type)
        if item_en:
            sql += " AND item_en = ?"
            params.append(item_en)
        sql += " ORDER BY report_date, item_cn"
        return self.conn.execute(sql, params).df()

    # === broker_reports（券商研报） ===
    def save_broker_reports(self, df: pd.DataFrame) -> int:
        """入库研报。

        重复入库时保留 DB 中已有的 pdf_path / ingested_to_rag（避免清空已下载状态）。
        """
        if df.empty:
            return 0
        df = df.copy()
        if "publish_date" in df.columns:
            df["publish_date"] = pd.to_datetime(df["publish_date"], errors="coerce").dt.date
        if "research_date" in df.columns:
            df["research_date"] = pd.to_datetime(df["research_date"], errors="coerce").dt.date
        df["updated_at"] = pd.Timestamp.now()

        # 从 DB 读出已有的 pdf_path / ingested_to_rag，回填到 df
        report_ids = df["report_id"].astype(str).tolist() if "report_id" in df.columns else []
        if report_ids:
            placeholders = ", ".join(["?"] * len(report_ids))
            existing = self.conn.execute(
                f"SELECT report_id, pdf_path, ingested_to_rag FROM broker_reports "
                f"WHERE report_id IN ({placeholders})",
                report_ids,
            ).df()
            if not existing.empty:
                existing = existing.rename(
                    columns={"pdf_path": "_existing_pdf_path", "ingested_to_rag": "_existing_ingested"}
                )
                df = df.merge(existing, on="report_id", how="left")
                # 仅在 df 中的值为空时，用 DB 已有的值回填
                if "pdf_path" in df.columns:
                    df["pdf_path"] = df["pdf_path"].where(
                        df["pdf_path"].astype(str) != "",
                        df["_existing_pdf_path"].astype(str).replace({"None": None, "nan": None}),
                    )
                if "ingested_to_rag" in df.columns:
                    df["ingested_to_rag"] = df["ingested_to_rag"].where(
                        df["ingested_to_rag"].notna() & (df["ingested_to_rag"] != False),
                        df["_existing_ingested"],
                    )
                df = df.drop(columns=["_existing_pdf_path", "_existing_ingested"], errors="ignore")

        cols = [
            "code", "stock_name", "title", "broker", "broker_code",
            "rating", "rating_prev", "rating_idx",
            "eps_forecast_y1", "eps_forecast_y2", "eps_forecast_y3",
            "pe_forecast_y1", "pe_forecast_y2", "pe_forecast_y3",
            "industry", "publish_date", "research_date",
            "info_code", "report_id", "pdf_path", "ingested_to_rag", "updated_at",
        ]
        df = df[[c for c in cols if c in df.columns]]
        self.upert_dataframe("broker_reports", df)
        return len(df)

    def load_broker_reports(
        self,
        code: str | None = None,
        need_pdf: bool | None = None,
        need_rag: bool | None = None,
    ) -> pd.DataFrame:
        sql = "SELECT * FROM broker_reports WHERE 1=1"
        params: list = []
        if code:
            sql += " AND code = ?"
            params.append(code)
        if need_pdf is True:
            sql += " AND pdf_path IS NOT NULL AND pdf_path != ''"
        elif need_pdf is False:
            sql += " AND (pdf_path IS NULL OR pdf_path = '')"
        if need_rag is True:
            sql += " AND ingested_to_rag = TRUE"
        elif need_rag is False:
            sql += " AND (ingested_to_rag IS NULL OR ingested_to_rag = FALSE)"
        sql += " ORDER BY publish_date DESC"
        return self.conn.execute(sql, params).df()

    def update_broker_report_pdf_path(self, report_id: str, pdf_path: str) -> None:
        self.conn.execute(
            "UPDATE broker_reports SET pdf_path = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE report_id = ?",
            [pdf_path, report_id],
        )

    def mark_broker_report_ingested(self, report_id: str) -> None:
        self.conn.execute(
            "UPDATE broker_reports SET ingested_to_rag = TRUE, "
            "updated_at = CURRENT_TIMESTAMP WHERE report_id = ?",
            [report_id],
        )

    # === eps_forecast_consensus（一致预期 EPS） ===
    def save_eps_forecast_consensus(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        df = df.copy()
        df["updated_at"] = pd.Timestamp.now()
        cols = [
            "code", "forecast_year", "broker_count",
            "eps_min", "eps_mean", "eps_max", "net_profit_mean", "updated_at",
        ]
        df = df[[c for c in cols if c in df.columns]]
        self.upert_dataframe("eps_forecast_consensus", df)
        return len(df)

    def load_eps_forecast_consensus(self, code: str) -> pd.DataFrame:
        return self.conn.execute(
            "SELECT * FROM eps_forecast_consensus WHERE code = ? ORDER BY forecast_year",
            [code],
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
