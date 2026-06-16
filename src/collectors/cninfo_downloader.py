"""从 cninfo 下载上市公司年报 PDF。

cninfo 是最权威的年报披露源，所有 A 股年报都在此披露。
PDF 下载链路：
1. POST /new/hisAnnouncement/query 拿到 adjunctUrl
2. GET http://static.cninfo.com.cn/{adjunctUrl} 下载 PDF
"""
from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import requests

from ..config import config

CNINFO_QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_DOWNLOAD_HOST = "http://static.cninfo.com.cn/"
CNINFO_STOCK_LIST_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"

# 交易所 → cninfo column 参数
COLUMN_MAP = {
    "sse": "sse",  # 上交所主板
    "szse": "szse",  # 深交所
}


class CnInfoDownloader:
    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or (config.CACHE_DIR / "cninfo")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
            }
        )
        # cninfo 不走代理（代理对 cninfo 不稳定）
        self.session.trust_env = False
        self._orgid_cache: dict[str, str] | None = None

    def _load_orgid_map(self) -> dict[str, str]:
        if self._orgid_cache is not None:
            return self._orgid_cache
        cache_file = self.cache_dir / "cninfo_stock_map.json"
        if cache_file.exists() and time.time() - cache_file.stat().st_mtime < 86400 * 7:
            with cache_file.open() as f:
                self._orgid_cache = json.load(f)
                return self._orgid_cache

        r = self.session.get(CNINFO_STOCK_LIST_URL, timeout=30)
        r.raise_for_status()
        data = r.json()
        mapping = {s["code"]: s["orgId"] for s in data.get("stockList", [])}
        with cache_file.open("w") as f:
            json.dump(mapping, f, ensure_ascii=False)
        self._orgid_cache = mapping
        return mapping

    def get_orgid(self, code: str) -> str:
        code = str(code).zfill(6)
        org_map = self._load_orgid_map()
        if code not in org_map:
            raise ValueError(f"找不到 {code} 的 orgId")
        return org_map[code]

    def query_annual_report(
        self, code: str, year: int = 2024
    ) -> Optional[dict]:
        """查询单只股票指定年份的年报。返回 adjunctUrl / 标题 等。"""
        code = str(code).zfill(6)
        orgid = self.get_orgid(code)
        column = "sse" if code.startswith("6") else (
            "szse" if code.startswith(("0", "3")) else "bj"
        )

        se_start = f"{year + 1}-01-01"
        se_end = f"{year + 1}-12-31"
        data = {
            "pageNum": "1",
            "pageSize": "10",
            "column": column,
            "tabName": "fulltext",
            "stock": f"{code},{orgid}",
            "category": "category_ndbg_szsh",
            "seDate": f"{se_start}~{se_end}",
        }

        # cninfo 对未编码的中文不友好，但我们的请求里没中文
        r = self.session.post(
            CNINFO_QUERY_URL,
            data=data,
            timeout=30,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        payload = r.json()
        anns = payload.get("announcements") or []
        # 找中文版的"年度报告"（非英文版、非摘要、非更正/修订/已取消）
        # cninfo 标题命名不统一，两种主流写法都接受：
        #   "XX公司2025年年度报告"（多数）
        #   "XX公司2025年度报告"（少数，如风神轮胎）
        keywords = (f"{year}年年度报告", f"{year}年度报告")
        skip_tokens = ("英文", "摘要", "已取消", "更正", "修订", "补充")
        for a in anns:
            title = a.get("announcementTitle", "")
            if any(k in title for k in keywords) and not any(t in title for t in skip_tokens):
                return a
        return None

    def download_annual_report(
        self,
        code: str,
        year: int = 2024,
        save_dir: Path | None = None,
        skip_if_exists: bool = True,
    ) -> Path:
        """下载年报 PDF。返回本地 PDF 路径。"""
        save_dir = save_dir or config.ANNUAL_REPORT_PDF_DIR
        save_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{code}_{year}_annual_report.pdf"
        save_path = save_dir / filename
        if skip_if_exists and save_path.exists() and save_path.stat().st_size > 100_000:
            return save_path

        ann = self.query_annual_report(code, year)
        if not ann:
            raise FileNotFoundError(f"{code} 找不到 {year} 年年度报告")

        adjunct_url = ann["adjunctUrl"]
        url = CNINFO_DOWNLOAD_HOST + adjunct_url

        r = self.session.get(url, timeout=120, stream=True)
        r.raise_for_status()
        with save_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
        return save_path
