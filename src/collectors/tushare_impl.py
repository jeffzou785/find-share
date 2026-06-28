"""P2-5 Tushare 兜底数据源（stub）。

设计目的（参见 IMPROVEMENTS P2-5）：
- DataSource Protocol 已就位，业务代码不直接依赖具体实现。
- 本模块提供 TushareSource 类作为兜底数据源 stub，**当且仅当** AkShare
  连续不稳定时才需要实际接入 Tushare 数据。
- 当前不实现实际网络调用：未配置 token 时实例化抛 NotImplementedError
  提示用户先配置；配置了 token 但未实现的方法也会抛 NotImplementedError。

激活步骤（未来真正要用时）：
1. pip install tushare
2. 在 .env 里设置 TUSHARE_TOKEN=<your_token>
3. 实现 TushareSource 各个方法（参考 akshare_impl.py 的返回结构）
4. 在 strategy / pipeline 入口把 source 替换为 TushareSource()

为什么是 stub 而非完整实现：
- 2000 积分需要付费，目前 AkShare 仍可用
- 业务代码已通过 DataSource Protocol 抽象，切换零代码改动
- 写 stub 锁定接口形状，未来真正接入时只需实现方法体

接口形状对齐 src/collectors/base.py 的 DataSource Protocol。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ..config import config


class TushareSource:
    """Tushare 兜底数据源（stub 实现）。

    所有方法签名与 AkShareSource 对齐，但当前不实际拉数据。
    未来接入时只需在每个方法里填实现，业务代码零修改。
    """

    def __init__(self, token: Optional[str] = None) -> None:
        self.token = token or config.TUSHARE_TOKEN
        # 不强制要求 token 存在——允许 import / 实例化用于类型检查。
        # 真正调用方法时才校验。

    def _ensure_ready(self) -> None:
        """调用任何方法前校验 token + tushare 包是否就绪。"""
        if not self.token:
            raise NotImplementedError(
                "TushareSource 是 stub 实现。要实际启用，需要：\n"
                "  1. pip install tushare\n"
                "  2. 在 .env 中设置 TUSHARE_TOKEN\n"
                "  3. 在本文件实现各方法（参考 akshare_impl.py 的返回结构）\n"
                "在此之前，业务代码应继续使用 AkShareSource。"
            )
        try:
            import tushare  # noqa: F401
        except ImportError:
            raise NotImplementedError(
                "未安装 tushare 包。pip install tushare 后再启用 TushareSource。"
            )

    # === 基础数据 ===
    def get_stock_list(self) -> pd.DataFrame:
        self._ensure_ready()
        raise NotImplementedError("TushareSource.get_stock_list 待实现")

    def get_sw_first_industry(self) -> pd.DataFrame:
        self._ensure_ready()
        raise NotImplementedError("TushareSource.get_sw_first_industry 待实现")

    def get_sw_second_industry(self) -> pd.DataFrame:
        self._ensure_ready()
        raise NotImplementedError("TushareSource.get_sw_second_industry 待实现")

    def get_stock_industry_mapping(self) -> pd.DataFrame:
        self._ensure_ready()
        raise NotImplementedError("TushareSource.get_stock_industry_mapping 待实现")

    # === 估值数据 ===
    def get_pe_pb_history(self, code: str, years: int = 5) -> pd.DataFrame:
        self._ensure_ready()
        raise NotImplementedError("TushareSource.get_pe_pb_history 待实现")

    # === 财务数据 ===
    def get_financial_abstract(self, code: str) -> pd.DataFrame:
        self._ensure_ready()
        raise NotImplementedError("TushareSource.get_financial_abstract 待实现")

    # === 披露日历 ===
    def get_disclosure_calendar(self, period: str = "2025年报") -> pd.DataFrame:
        self._ensure_ready()
        raise NotImplementedError("TushareSource.get_disclosure_calendar 待实现")
