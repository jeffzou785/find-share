"""P2：screen_run 前瞻收益验证。

只做本地、可复现的计算：给定候选、run 日期和价格序列，计算
20/60/120 个交易日后的绝对收益和可选基准相对收益。
"""
from __future__ import annotations

from typing import Iterable, Sequence

import pandas as pd


DEFAULT_WINDOWS: tuple[int, ...] = (20, 60, 120)
DEFAULT_STATUSES: tuple[str, ...] = ("hit", "watch")


def normalize_windows(windows: Sequence[int]) -> tuple[int, ...]:
    """校验并去重窗口，保留传入顺序。"""
    normalized: list[int] = []
    seen: set[int] = set()
    for raw in windows:
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid window: {raw!r}") from exc
        if value <= 0:
            raise ValueError(f"window_days must be positive, got {value}")
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    if not normalized:
        raise ValueError("at least one window is required")
    return tuple(normalized)


def _prepare_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """标准化价格序列，要求至少有 date / close 两列。"""
    if prices.empty or "date" not in prices.columns or "close" not in prices.columns:
        return pd.DataFrame(columns=["date", "close"])
    df = prices[["date", "close"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"])
    df = df[df["close"] > 0]
    if df.empty:
        return pd.DataFrame(columns=["date", "close"])
    return (
        df.sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
        .reset_index(drop=True)
    )


def _first_trade_index_on_or_after(
    prices: pd.DataFrame,
    anchor_date: pd.Timestamp,
    max_start_lag_days: int,
) -> tuple[int | None, str | None]:
    if prices.empty:
        return None, "price_history_missing"
    idx = prices.index[prices["date"] >= anchor_date]
    if len(idx) == 0:
        return None, "start_price_missing"
    start_idx = int(idx[0])
    start_date = prices.iloc[start_idx]["date"]
    lag_days = int((start_date - anchor_date).days)
    if lag_days > max_start_lag_days:
        return None, f"start_price_lag_gt_{max_start_lag_days}d"
    return start_idx, None


def _window_return(
    prices: pd.DataFrame,
    *,
    start_idx: int,
    window_days: int,
) -> tuple[dict, str | None]:
    end_idx = start_idx + int(window_days)
    if end_idx >= len(prices):
        return {}, "insufficient_future_price"
    start = prices.iloc[start_idx]
    end = prices.iloc[end_idx]
    start_close = float(start["close"])
    end_close = float(end["close"])
    if start_close <= 0:
        return {}, "invalid_start_close"
    return {
        "start_date": start["date"],
        "end_date": end["date"],
        "start_close": start_close,
        "end_close": end_close,
        "absolute_return": end_close / start_close - 1.0,
    }, None


def _benchmark_return(
    prepared_benchmark_prices: pd.DataFrame | None,
    *,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> tuple[float | None, str | None]:
    if prepared_benchmark_prices is None:
        return None, None
    bench = prepared_benchmark_prices
    if bench.empty:
        return None, "benchmark_price_history_missing"
    start_rows = bench[bench["date"] >= start_date]
    end_rows = bench[bench["date"] >= end_date]
    if start_rows.empty or end_rows.empty:
        return None, "benchmark_window_missing"
    start_close = float(start_rows.iloc[0]["close"])
    end_close = float(end_rows.iloc[0]["close"])
    if start_close <= 0:
        return None, "benchmark_invalid_start_close"
    return end_close / start_close - 1.0, None


def compute_forward_returns(
    *,
    candidate: dict,
    anchor_date: pd.Timestamp | str,
    price_history: pd.DataFrame,
    windows: Sequence[int] = DEFAULT_WINDOWS,
    benchmark_history: pd.DataFrame | None = None,
    benchmark_code: str | None = None,
    max_start_lag_days: int = 10,
) -> list[dict]:
    """计算单个候选在多个交易日窗口后的收益。

    windows 使用交易日序号而不是自然日：第 20 个窗口表示进入价后的第 20
    条价格记录。
    """
    code = str(candidate.get("code", "")).zfill(6)
    anchor = pd.to_datetime(anchor_date).normalize()
    windows = normalize_windows(windows)
    if max_start_lag_days < 0:
        raise ValueError("max_start_lag_days must be >= 0")
    prices = _prepare_prices(price_history)
    benchmark_prices = (
        _prepare_prices(benchmark_history)
        if benchmark_history is not None else None
    )
    if benchmark_code and benchmark_history is None:
        benchmark_prices = pd.DataFrame(columns=["date", "close"])
    start_idx, start_error = _first_trade_index_on_or_after(
        prices, anchor, max_start_lag_days=max_start_lag_days,
    )

    rows: list[dict] = []
    for window in windows:
        row = {
            "run_id": candidate.get("run_id"),
            "code": code,
            "name": candidate.get("name"),
            "strategy": candidate.get("strategy"),
            "period": candidate.get("period"),
            "window_days": int(window),
            "anchor_date": anchor,
            "benchmark_code": benchmark_code,
            "status": "ok",
            "error": None,
        }
        if start_idx is None:
            row["status"] = "missing"
            row["error"] = start_error
            rows.append(row)
            continue

        values, error = _window_return(prices, start_idx=start_idx, window_days=window)
        if error:
            row["status"] = "missing"
            row["error"] = error
            row["start_date"] = prices.iloc[start_idx]["date"]
            row["start_close"] = float(prices.iloc[start_idx]["close"])
            rows.append(row)
            continue

        row.update(values)
        bench_ret, bench_error = _benchmark_return(
            benchmark_prices,
            start_date=values["start_date"],
            end_date=values["end_date"],
        )
        if bench_error:
            row["status"] = "partial"
            row["error"] = bench_error
        row["benchmark_return"] = bench_ret
        row["relative_return"] = (
            row["absolute_return"] - bench_ret
            if bench_ret is not None else None
        )
        rows.append(row)
    return rows


def compute_forward_returns_batch(
    *,
    candidates: pd.DataFrame,
    anchor_date: pd.Timestamp | str,
    price_loader,
    windows: Sequence[int] = DEFAULT_WINDOWS,
    benchmark_history: pd.DataFrame | None = None,
    benchmark_code: str | None = None,
    statuses: Iterable[str] = DEFAULT_STATUSES,
    max_start_lag_days: int = 10,
) -> pd.DataFrame:
    """批量计算候选收益。

    price_loader(code) 应返回该 code 的价格 DataFrame。默认只对 hit/watch
    计算，避免 rejected/data_missing 扰乱验证样本。
    """
    if candidates.empty:
        return pd.DataFrame()
    windows = normalize_windows(windows)
    if max_start_lag_days < 0:
        raise ValueError("max_start_lag_days must be >= 0")
    allowed = set(statuses)
    rows: list[dict] = []
    filtered = (
        candidates[candidates["status"].isin(allowed)].copy()
        if "status" in candidates.columns else candidates.copy()
    )
    for _, candidate_row in filtered.iterrows():
        candidate = candidate_row.to_dict()
        code = str(candidate.get("code", "")).zfill(6)
        price_history = price_loader(code)
        rows.extend(
            compute_forward_returns(
                candidate=candidate,
                anchor_date=anchor_date,
                price_history=price_history,
                windows=windows,
                benchmark_history=benchmark_history,
                benchmark_code=benchmark_code,
                max_start_lag_days=max_start_lag_days,
            )
        )
    return pd.DataFrame(rows)


def summarize_backtest(results: pd.DataFrame) -> dict:
    """生成按窗口聚合的简短摘要。"""
    if results.empty:
        return {"total_rows": 0, "windows": {}}
    out: dict = {"total_rows": int(len(results)), "windows": {}}
    for window, group in results.groupby("window_days"):
        ok = group[group["status"].isin(["ok", "partial"])]
        out["windows"][int(window)] = {
            "rows": int(len(group)),
            "ok_rows": int(len(ok)),
            "missing_rows": int((group["status"] == "missing").sum()),
            "avg_absolute_return": (
                round(float(ok["absolute_return"].mean()), 6)
                if "absolute_return" in ok and ok["absolute_return"].notna().any()
                else None
            ),
            "avg_relative_return": (
                round(float(ok["relative_return"].mean()), 6)
                if "relative_return" in ok and ok["relative_return"].notna().any()
                else None
            ),
        }
    return out
