"""collectors 包入口。"""
from .base import DataSource  # noqa: F401
from .a_stock_skill_source import AStockSkillSource  # noqa: F401
from .cached_impl import LocalCachedSource  # noqa: F401
from .neglect_evidence import NeglectEvidenceCollector  # noqa: F401
from .sina_impl import SinaFinancialSource  # noqa: F401
from .eastmoney_research import EastMoneyResearchSource  # noqa: F401
from .ths_forecast import ThsForecastSource  # noqa: F401
from .tushare_impl import TushareSource  # noqa: F401
from .global_stock_mapping import (  # noqa: F401
    GLOBAL_STOCK_MAPPING_COLUMNS,
    HKStockMapping,
    build_hk_mapping,
    build_hk_mapping_frame,
    hk_eastmoney_secucode,
    hk_eastmoney_secid,
    hk_yahoo_symbol,
    normalize_a_code,
    normalize_hk_code,
)

_AKSHARE_IMPORT_ERROR = None
try:
    from .akshare_impl import AkShareSource  # noqa: F401
except ModuleNotFoundError as exc:
    _AKSHARE_IMPORT_ERROR = exc

    class AkShareSource:  # type: ignore[no-redef]
        """AkShare optional fallback.

        新数据路径默认使用 AStockSkillSource；只有显式实例化 AkShareSource 时才要求
        安装 akshare。
        """

        def __init__(self, *args, **kwargs):
            raise ModuleNotFoundError(
                "akshare 未安装；默认请使用 AStockSkillSource"
            ) from _AKSHARE_IMPORT_ERROR
