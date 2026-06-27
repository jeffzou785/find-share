"""collectors 包入口。"""
from .base import DataSource  # noqa: F401
from .akshare_impl import AkShareSource  # noqa: F401
from .cached_impl import LocalCachedSource  # noqa: F401
from .neglect_evidence import NeglectEvidenceCollector  # noqa: F401
from .sina_impl import SinaFinancialSource  # noqa: F401
from .eastmoney_research import EastMoneyResearchSource  # noqa: F401
from .ths_forecast import ThsForecastSource  # noqa: F401
