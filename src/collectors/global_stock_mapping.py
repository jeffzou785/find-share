"""A/H/港股代码格式归一。

供 `$global-stock-data` 港股行情、K线、三表、资金流等接口复用。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


HK_MARKET_PREFIX = "116"


@dataclass(frozen=True)
class HKStockMapping:
    a_code: Optional[str]
    hk_code: str
    yahoo_symbol: str
    eastmoney_secucode: str
    eastmoney_secid: str
    name: Optional[str] = None
    hk_disclosure_source_gap: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_a_code(code: str | int | None) -> Optional[str]:
    if code is None or str(code).strip() == "":
        return None
    digits = "".join(ch for ch in str(code).strip() if ch.isdigit())
    if not digits:
        return None
    return digits.zfill(6)[-6:]


def normalize_hk_code(code: str | int) -> str:
    raw = str(code).strip().upper()
    raw = raw.replace("HK:", "").replace("HK", "").replace(".HK", "")
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits or len(digits) > 5:
        raise ValueError(f"invalid hk code: {code!r}")
    return digits.zfill(5)


def hk_yahoo_symbol(code: str | int) -> str:
    hk_code = normalize_hk_code(code)
    yahoo_code = hk_code[1:] if hk_code.startswith("0") else hk_code
    return f"{yahoo_code}.HK"


def hk_eastmoney_secucode(code: str | int) -> str:
    return f"{normalize_hk_code(code)}.HK"


def hk_eastmoney_secid(code: str | int) -> str:
    return f"{HK_MARKET_PREFIX}.{normalize_hk_code(code)}"


def build_hk_mapping(
    *,
    hk_code: str | int,
    a_code: str | int | None = None,
    name: Optional[str] = None,
) -> HKStockMapping:
    normalized_hk = normalize_hk_code(hk_code)
    return HKStockMapping(
        a_code=normalize_a_code(a_code),
        hk_code=normalized_hk,
        yahoo_symbol=hk_yahoo_symbol(normalized_hk),
        eastmoney_secucode=hk_eastmoney_secucode(normalized_hk),
        eastmoney_secid=hk_eastmoney_secid(normalized_hk),
        name=name,
    )
