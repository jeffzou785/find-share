"""AkShare 调用的重试/缓存装饰器。"""
from __future__ import annotations

import functools
import hashlib
import pickle
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import config


class AkShareTransientError(Exception):
    """AkShare 调用偶发性错误（限流、超时、字段临时不可用）。"""


def _build_retry_decorator(max_retries: int, base_delay: float):
    return retry(
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=base_delay, min=base_delay, max=base_delay * 10),
        retry=retry_if_exception_type(AkShareTransientError),
        reraise=True,
    )


def akshare_call(func: Callable) -> Callable:
    """装饰器：包裹 AkShare 调用，提供重试 + 缓存。

    缓存键基于函数名 + 参数 hash；缓存以 pickle 形式存于 CACHE_DIR。
    成功调用的 DataFrame 缓存 1 天，可以通过 `force_refresh=True` 跳过。
    """
    retry_decorator = _build_retry_decorator(
        config.AKSHARE_MAX_RETRIES, config.AKSHARE_RETRY_BASE_DELAY
    )

    @functools.wraps(func)
    def wrapper(*args: Any, force_refresh: bool = False, **kwargs: Any) -> pd.DataFrame:
        cache_key = _make_cache_key(func.__name__, args, kwargs)
        cache_path = config.CACHE_DIR / f"{cache_key}.pkl"

        if not force_refresh and cache_path.exists():
            age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
            if age_hours < 24:
                try:
                    with cache_path.open("rb") as f:
                        return pickle.load(f)
                except Exception:
                    cache_path.unlink(missing_ok=True)

        retried = retry_decorator(func)
        try:
            df = retried(*args, **kwargs)
        except RetryError as e:
            raise AkShareTransientError(
                f"{func.__name__} 重试 {config.AKSHARE_MAX_RETRIES} 次后仍失败: {e}"
            ) from e

        if df is None:
            raise AkShareTransientError(f"{func.__name__} 返回 None（接口异常或字段漂移）")

        # 缓存
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with cache_path.open("wb") as f:
                pickle.dump(df, f)
        except Exception:
            pass

        return df

    return wrapper


def _make_cache_key(func_name: str, args: tuple, kwargs: dict) -> str:
    key_str = f"{func_name}|{args!r}|{sorted(kwargs.items())!r}"
    return hashlib.md5(key_str.encode("utf-8")).hexdigest()[:16]
