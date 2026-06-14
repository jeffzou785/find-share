"""东财 F10 客户端（走 emweb.eastmoney.com，不走 push2）。

用于补全行业映射、获取公司基本面。

接口：
- CompanySurvey: 公司简介（含 EM2016 行业 + CSRC 行业）
- 主要走 emweb.securities.eastmoney.com 域名，TLS 不被代理拦截
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import requests

from .base import normalize_code, with_market_prefix


EMWEB_COMPANY_SURVEY = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax"
)


@dataclass
class CompanyInfo:
    code: str
    name: str
    em2016: str  # 东财行业三级（如 "食品饮料-饮料-白酒"）
    em2016_first: str  # 东财一级行业名
    em2016_second: str  # 东财二级行业名
    csrc: str  # 证监会行业大类
    csrc_section: str  # 证监会行业门类（如 "制造业"、"金融业"）
    province: str


class EmWebClient:
    def __init__(self, max_workers: int = 8, rate_limit_per_sec: float = 5.0):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://emweb.securities.eastmoney.com/",
            }
        )
        # emweb 不走代理（代理对东财部分域名不稳定）
        self.session.trust_env = False
        self.max_workers = max_workers
        self._min_interval = 1.0 / rate_limit_per_sec if rate_limit_per_sec > 0 else 0

    def get_company_info(self, code: str) -> Optional[CompanyInfo]:
        """单只股票的公司信息（含行业）。"""
        em_code = with_market_prefix(code)  # SH600519
        url = f"{EMWEB_COMPANY_SURVEY}?code={em_code}"
        try:
            r = self.session.get(url, timeout=15)
            r.raise_for_status()
            d = r.json()
        except Exception:
            return None

        jbzl_list = d.get("jbzl") or []
        if not jbzl_list:
            return None
        jbzl = jbzl_list[0] if isinstance(jbzl_list, list) else jbzl_list

        em2016 = jbzl.get("EM2016") or ""
        parts = em2016.split("-") if em2016 else []
        em_first = parts[0] if len(parts) >= 1 else ""
        em_second = parts[1] if len(parts) >= 2 else ""

        csrc_full = jbzl.get("INDUSTRYCSRC1") or ""
        # 拆门类和大类：如 "制造业-酒、饮料和精制茶制造业"
        csrc_parts = csrc_full.split("-", 1) if csrc_full else []
        csrc_section = csrc_parts[0] if csrc_parts else ""
        csrc_detail = csrc_parts[1] if len(csrc_parts) >= 2 else csrc_full

        return CompanyInfo(
            code=normalize_code(code),
            name=jbzl.get("SECURITY_NAME_ABBR") or "",
            em2016=em2016,
            em2016_first=em_first,
            em2016_second=em_second,
            csrc=csrc_detail,
            csrc_section=csrc_section,
            province=jbzl.get("PROVINCE") or "",
        )

    def get_company_info_batch(
        self,
        codes: list[str],
        show_progress: bool = True,
    ) -> pd.DataFrame:
        """批量拉公司信息（并发）。返回 DataFrame。"""
        results: list[CompanyInfo | None] = [None] * len(codes)

        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            future_to_idx = {
                ex.submit(self.get_company_info, c): i for i, c in enumerate(codes)
            }

            iterator = as_completed(future_to_idx)
            if show_progress:
                from tqdm import tqdm
                iterator = tqdm(
                    iterator, total=len(future_to_idx), desc="东财行业映射", ncols=80
                )

            for fut in iterator:
                idx = future_to_idx[fut]
                try:
                    results[idx] = fut.result()
                except Exception:
                    results[idx] = None

        rows = [r.__dict__ for r in results if r is not None]
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)
