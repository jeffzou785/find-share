"""东财研报数据源（reportapi.eastmoney.com）。

提供：
- 研报列表（含评级 / 三年 EPS 预测 / 机构 / 日期 / 行业分类）
- 研报 PDF 下载（pdf.dfcfw.com，带 Referer 鉴权）

字段映射参考 a-stock-data SKILL.md §2.1。已加东财统一限流（_em_throttle.em_get）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from ..config import config
from ._em_throttle import REPORT_API, PDF_TPL, UA, em_get


# 东财 record 原始字段 → snake_case
RECORD_FIELD_MAP: dict[str, str] = {
    "code": "code",
    "stockName": "stock_name",
    "title": "title",
    "orgSName": "broker",
    "orgCode": "broker_code",
    "publishDate": "publish_date",
    "emRatingName": "rating",
    "lastEmRatingName": "rating_prev",
    "emRatingValue": "rating_idx",
    "indvInduName": "industry",
    "predictThisYearEps": "eps_forecast_y1",
    "predictNextYearEps": "eps_forecast_y2",
    "predictNextTwoYearEps": "eps_forecast_y3",
    "predictThisYearPe": "pe_forecast_y1",
    "predictNextYearPe": "pe_forecast_y2",
    "predictNextTwoYearPe": "pe_forecast_y3",
    "researchDate": "research_date",
    "infoCode": "info_code",
    "count": "institution_count",
}


def _safe_filename(text: str, max_len: int = 80) -> str:
    """清洗文件名：去掉非法字符，截断长度。"""
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", text)
    return text[:max_len].strip(" .")


class EastMoneyResearchSource:
    """东财研报数据源。"""

    def __init__(self, pdf_dir: Path | None = None):
        self.pdf_dir = pdf_dir or (config.RESEARCH_REPORT_DIR / "broker")

    def get_reports(
        self,
        code: str,
        max_pages: int = 5,
        page_size: int = 100,
    ) -> pd.DataFrame:
        """拉取指定股票的研报列表。

        返回 DataFrame，每行一份研报。字段映射见 RECORD_FIELD_MAP。
        含 report_id（去重键，由 info_code 生成）。
        """
        code = str(code).strip().zfill(6)
        all_records: list[dict] = []

        for page in range(1, max_pages + 1):
            params = {
                "industryCode": "*",
                "pageSize": str(page_size),
                "industry": "*",
                "rating": "*",
                "ratingChange": "*",
                "beginTime": "2000-01-01",
                "endTime": "2030-01-01",
                "pageNo": str(page),
                "fields": "",
                "qType": "0",
                "orgCode": "",
                "code": code,
                "rcode": "",
                "p": str(page),
                "pageNum": str(page),
                "pageNumber": str(page),
            }
            r = em_get(
                REPORT_API,
                params=params,
                headers={"Referer": "https://data.eastmoney.com/"},
                timeout=30,
            )
            if r.status_code != 200:
                break
            try:
                d = r.json()
            except ValueError:
                break

            rows = d.get("data") or []
            if not rows:
                break

            all_records.extend(rows)

            total_pages = d.get("TotalPage", 1) or 1
            if page >= total_pages:
                break

        if not all_records:
            return pd.DataFrame()

        df = pd.DataFrame(all_records)
        # 东财 record 不返回 code/stockName（请求时传入），手动补回
        df["code"] = code
        if "stockName" not in df.columns:
            df["stockName"] = ""

        # 重命名 + 保留未映射的列
        rename_map = {k: v for k, v in RECORD_FIELD_MAP.items() if k in df.columns}
        df = df.rename(columns=rename_map)

        # 关键字段规整
        if "publish_date" in df.columns:
            df["publish_date"] = pd.to_datetime(
                df["publish_date"], errors="coerce"
            ).dt.date
        if "research_date" in df.columns:
            df["research_date"] = pd.to_datetime(
                df["research_date"], errors="coerce"
            ).dt.date

        # 数值字段转 float
        for col in (
            "eps_forecast_y1",
            "eps_forecast_y2",
            "eps_forecast_y3",
            "pe_forecast_y1",
            "pe_forecast_y2",
            "pe_forecast_y3",
            "rating_idx",
            "institution_count",
        ):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # report_id 去重键：info_code 是东财唯一标识
        if "info_code" in df.columns:
            df["report_id"] = df["info_code"].astype(str)
            df = df.drop_duplicates(subset=["report_id"], keep="first")

        # pdf_path / ingested_to_rag 默认空
        df["pdf_path"] = ""
        df["ingested_to_rag"] = False

        return df.reset_index(drop=True)

    def download_pdf(
        self,
        record: dict | pd.Series,
        target_dir: Path | None = None,
        skip_if_exists: bool = True,
    ) -> Path | None:
        """下载单份研报 PDF。

        record 需含 info_code / publish_date / broker / title。
        文件命名：{publish_date}_{broker}_{title}.pdf（清洗后）
        """
        info_code = record.get("info_code", "")
        if not info_code:
            return None

        target_dir = target_dir or self.pdf_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        date = ""
        if record.get("publish_date") is not None:
            date = str(record["publish_date"])[:10]
        broker = _safe_filename(record.get("broker") or "未知", 30)
        title = _safe_filename(record.get("title") or "", 60)

        fname = f"{date}_{broker}_{title}.pdf" if date else f"{broker}_{title}.pdf"
        target = target_dir / fname

        if skip_if_exists and target.exists() and target.stat().st_size >= 1024:
            return target

        url = PDF_TPL.format(info_code=info_code)
        r = em_get(
            url,
            headers={"Referer": "https://data.eastmoney.com/"},
            timeout=60,
        )
        if r.status_code == 200 and len(r.content) >= 1024:
            target.write_bytes(r.content)
            return target
        return None
