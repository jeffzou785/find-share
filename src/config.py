"""全局配置，从环境变量加载。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Config:
    PROJECT_ROOT: Path = Path(os.getenv("PROJECT_ROOT", ".")).resolve()
    DUCKDB_PATH: Path = PROJECT_ROOT / os.getenv("DUCKDB_PATH", "data/duckdb/find_share.duckdb")
    ANNUAL_REPORT_PDF_DIR: Path = PROJECT_ROOT / os.getenv(
        "ANNUAL_REPORT_PDF_DIR", "data/pdfs/annual_reports"
    )
    RESEARCH_REPORT_DIR: Path = PROJECT_ROOT / os.getenv("RESEARCH_REPORT_DIR", "research_reports")
    CACHE_DIR: Path = PROJECT_ROOT / os.getenv("CACHE_DIR", "data/cache")
    EXPORTS_DIR: Path = PROJECT_ROOT / "data/exports"

    AKSHARE_MAX_RETRIES: int = int(os.getenv("AKSHARE_MAX_RETRIES", "3"))
    AKSHARE_RETRY_BASE_DELAY: float = float(os.getenv("AKSHARE_RETRY_BASE_DELAY", "1.0"))

    TUSHARE_TOKEN: str = os.getenv("TUSHARE_TOKEN", "")

    @classmethod
    def ensure_dirs(cls) -> None:
        for d in [
            cls.DUCKDB_PATH.parent,
            cls.ANNUAL_REPORT_PDF_DIR,
            cls.RESEARCH_REPORT_DIR,
            cls.CACHE_DIR,
            cls.EXPORTS_DIR,
        ]:
            d.mkdir(parents=True, exist_ok=True)


config = Config()
config.ensure_dirs()
