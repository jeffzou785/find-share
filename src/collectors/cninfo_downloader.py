"""从 cninfo 下载上市公司财报 PDF（年报 / 半年报 / 季报）。

cninfo 是最权威的财报披露源，所有 A 股定期报告都在此披露。
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

# 定期报告 → cninfo category 参数
REPORT_CATEGORY: dict[str, str] = {
    "annual": "category_ndbg_szsh",     # 年度报告
    "half_year": "category_bndbg_szsh", # 半年度报告
    "q1": "category_yjdbg_szsh",        # 一季度报告
    "q3": "category_sjdbg_szsh",        # 三季度报告
}

# 定期报告 → 标题关键词（多种命名变体都覆盖）
REPORT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "annual": ("年年度报告", "年度报告"),
    "half_year": ("年半年度报告", "半年度报告"),
    "q1": ("年第一季度报告", "年一季度报告", "年一季报"),
    "q3": ("年第三季度报告", "年三季度报告", "年三季报"),
}

# 定期报告披露窗口（用于 cninfo seDate 过滤）
# 年份 N 的报告实际披露时间窗口
REPORT_SEARCH_WINDOW: dict[str, tuple[int, int, int, int]] = {
    # (start_month, start_day, end_month, end_day) 相对年份 N 的偏移
    # 年报 N：N+1 年 1-12 月
    "annual": (1, 1, 12, 31),
    # 半年报 N：N 年 7-12 月（实际多在 8 月披露）
    "half_year": (7, 1, 12, 31),
    # 一季报 N：N 年 4-6 月
    "q1": (4, 1, 6, 30),
    # 三季报 N：N 年 10-12 月
    "q3": (10, 1, 12, 31),
}

# 所有报告类型通用排除词（英文版/摘要/已取消/更正/修订/补充）
REPORT_SKIP_TOKENS: tuple[str, ...] = (
    "英文", "摘要", "已取消", "更正", "修订", "补充",
)


def build_pdf_filename(code: str, year: int, report_type: str) -> str:
    """生成定期报告 PDF 的 canonical 文件名。

    - 年报：{code}_{year}_annual_report.pdf（与历史存量命名一致）
    - 半年报/季报：{code}_{year}_{report_type}.pdf
    """
    code = str(code).zfill(6)
    if report_type == "annual":
        return f"{code}_{year}_annual_report.pdf"
    return f"{code}_{year}_{report_type}.pdf"


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
        """查询单只股票指定年份的年报。向后兼容入口。"""
        return self.query_report(code, year, report_type="annual")

    def query_report(
        self, code: str, year: int, report_type: str = "annual"
    ) -> Optional[dict]:
        """查询单只股票指定年份的定期报告（annual/half_year/q1/q3）。

        返回 cninfo announcement dict（含 adjunctUrl、announcementTitle 等）。
        """
        if report_type not in REPORT_CATEGORY:
            raise ValueError(
                f"未知 report_type={report_type}，必须为 {list(REPORT_CATEGORY)}"
            )
        code = str(code).zfill(6)
        orgid = self.get_orgid(code)
        column = "sse" if code.startswith("6") else (
            "szse" if code.startswith(("0", "3")) else "bj"
        )

        # 搜索窗口：年报在 N+1 年披露，季报/半年报在 N 当年披露
        sm, sd, em, ed = REPORT_SEARCH_WINDOW[report_type]
        search_year = year + 1 if report_type == "annual" else year
        se_start = f"{search_year}-{sm:02d}-{sd:02d}"
        se_end = f"{search_year}-{em:02d}-{ed:02d}"

        data = {
            "pageNum": "1",
            "pageSize": "30",
            "column": column,
            "tabName": "fulltext",
            "stock": f"{code},{orgid}",
            "category": REPORT_CATEGORY[report_type],
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

        keywords = tuple(f"{year}{kw}" for kw in REPORT_KEYWORDS[report_type])
        for a in anns:
            title = a.get("announcementTitle", "")
            if any(k in title for k in keywords) and not any(
                t in title for t in REPORT_SKIP_TOKENS
            ):
                return a
        return None

    def download_annual_report(
        self,
        code: str,
        year: int = 2024,
        save_dir: Path | None = None,
        skip_if_exists: bool = True,
    ) -> Path:
        """下载年报 PDF。向后兼容入口。"""
        return self.download_report(
            code, year, report_type="annual",
            save_dir=save_dir, skip_if_exists=skip_if_exists,
        )

    def download_report(
        self,
        code: str,
        year: int,
        report_type: str = "annual",
        save_dir: Path | None = None,
        skip_if_exists: bool = True,
    ) -> Path:
        """下载定期报告 PDF（annual/half_year/q1/q3）。返回本地 PDF 路径。

        文件名约定：
        - 年报：{code}_{year}_annual_report.pdf（canonical，与历史存量文件一致）
        - 半年报/季报：{code}_{year}_{report_type}.pdf
        - skip_if_exists 时同时检查 legacy 年报命名 _annual.pdf，避免重复下载
        """
        save_dir = save_dir or config.ANNUAL_REPORT_PDF_DIR
        save_dir.mkdir(parents=True, exist_ok=True)

        canonical = build_pdf_filename(code, year, report_type)
        save_path = save_dir / canonical
        if skip_if_exists and save_path.exists() and save_path.stat().st_size > 100_000:
            return save_path

        # 兼容 legacy 年报命名 _annual.pdf
        if skip_if_exists and report_type == "annual":
            legacy = save_dir / f"{code}_{year}_annual.pdf"
            if legacy.exists() and legacy.stat().st_size > 100_000:
                return legacy

        ann = self.query_report(code, year, report_type=report_type)
        if not ann:
            type_cn = {"annual": "年报", "half_year": "半年报", "q1": "一季报", "q3": "三季报"}[report_type]
            raise FileNotFoundError(f"{code} 找不到 {year} {type_cn}")

        adjunct_url = ann["adjunctUrl"]
        url = CNINFO_DOWNLOAD_HOST + adjunct_url

        r = self.session.get(url, timeout=120, stream=True)
        r.raise_for_status()
        with save_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
        return save_path
