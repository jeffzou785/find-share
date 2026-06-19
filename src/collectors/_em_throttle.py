"""东财系接口统一限流封装（reportapi / datacenter / push2）。

设计要点：
- 串行限流（最小间隔 + 随机抖动），避免触发东财风控（每秒>5次/并发≥10 会临时封 IP）
- Session 复用（Keep-Alive）
- trust_env=False 跳过系统代理（Clash Verge 对东财域名不稳定，参见 [[feedback-network-environment]] 坑 2）

与 emweb_client.py 解耦：emweb_client 走 emweb.securities.eastmoney.com（F10 公司资料），
本模块走 reportapi.eastmoney.com / datacenter-web.eastmoney.com（研报/数据）。
两个 Session 独立，避免 cookie/headers 互污染。
"""
from __future__ import annotations

import os
import random
import time

import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_API = "https://reportapi.eastmoney.com/report/list"
PDF_TPL = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"

# 东财请求最小间隔（秒）。批量任务建议通过环境变量调大到 1.5~2。
EM_MIN_INTERVAL = float(os.getenv("EM_MIN_INTERVAL", "1.0"))

EM_SESSION = requests.Session()
EM_SESSION.headers.update({"User-Agent": UA})
# 东财域名不走系统代理（Clash Verge 对东财不稳定）
EM_SESSION.trust_env = False

_em_last_call: list[float] = [0.0]


def em_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
    **kwargs,
) -> requests.Response:
    """东财统一请求入口：自动节流 + 复用 session + 默认不走代理。

    所有 eastmoney.com / dfcfw.com 接口都应通过它请求。
    """
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    try:
        return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()


def eastmoney_datacenter(
    report_name: str,
    columns: str = "ALL",
    filter_str: str = "",
    page_size: int = 50,
    sort_columns: str = "",
    sort_types: str = "-1",
) -> list[dict]:
    """东财数据中心统一查询（已内置限流）。

    用于龙虎榜/解禁/融资融券等数据查询。当前 find-share 暂未用到，留作扩展。
    """
    params = {
        "reportName": report_name,
        "columns": columns,
        "filter": filter_str,
        "pageNumber": "1",
        "pageSize": str(page_size),
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "source": "WEB",
        "client": "WEB",
    }
    r = em_get(DATACENTER_URL, params=params, timeout=15)
    d = r.json()
    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []
