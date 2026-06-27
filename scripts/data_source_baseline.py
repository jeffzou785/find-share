"""P1-5a 数据源 baseline 对照工具。

目的（参见 IMPROVEMENTS P1-4/P1-5）：
- 在切换/扩数据源前，对 30-50 只样本股的关键指标做"AkShare vs 新浪"对照
- 输出差异分布表 + 不可替换字段清单（diff > 阈值的字段进入"不可替换"）
- 防止数据源切换导致策略命中清单静默漂移

用法：
    # 默认从候选池抽 30 只（覆盖各行业）
    python3 scripts/data_source_baseline.py --sample-size 30

    # 指定股票
    python3 scripts/data_source_baseline.py --codes 600031 601058 000858

    # 写入指定目录
    python3 scripts/data_source_baseline.py --output-dir data/baselines

输出：
    {output-dir}/baseline_{YYYYMMDD}/
      raw_diff.csv         每只股票每指标的差异明细
      summary.md           差异分布报告 + 不可替换字段清单
      samples.json         本次抽样的股票列表

约束：
- 默认串行（避免东财限流）
- 单股失败不中断全局
- 网络异常走 source_status='error'，差异列留空
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.collectors import AkShareSource
from src.collectors.sina_impl import SinaFinancialSource
from src.config import config


# 对照字段：指标名 → (akshare 字段, sina item_en)
COMPARE_FIELDS = [
    ("revenue", None, "revenue"),            # 营收（akshare 直接字段，sina 利润表 revenue）
    ("net_profit", None, "net_profit"),      # 净利润
    ("deducted_net_profit", None, "deducted_net_profit"),  # 扣非净利
]

# 差异阈值：超过这个比例进入"不可替换"清单
DIFF_THRESHOLD_PCT = 0.05  # 5%


def _sample_codes(store, n: int) -> list[str]:
    """从候选池抽 n 只，覆盖各行业。"""
    df = store.load_stock_industry()
    if df.empty:
        return []
    # 每个行业抽 N/总行业数 只
    if "sw_first" not in df.columns:
        return df["code"].astype(str).str.zfill(6).head(n).tolist()
    by_industry = df.groupby("sw_first").head(max(1, n // max(1, df["sw_first"].nunique())))
    return by_industry["code"].astype(str).str.zfill(6).head(n).tolist()


def _get_akshare_fin(ak: AkShareSource, code: str) -> tuple[dict, Optional[int]]:
    """从 AkShare 拿最新年报的财务指标 + 年份。

    返回 (指标 dict, 最新年报的年份)。失败时指标 dict 全为 None，年份为 None。
    """
    out = {"revenue": None, "net_profit": None, "deducted_net_profit": None}
    try:
        fin = ak.get_financial_abstract(code)
    except Exception:
        return out, None
    if fin.empty or "report_date" not in fin.columns:
        return out, None
    fin = fin.copy()
    fin["report_date"] = pd.to_datetime(fin["report_date"], errors="coerce")
    annual = fin[fin["report_date"].dt.month == 12]
    if annual.empty:
        annual = fin
    latest = annual.sort_values("report_date").iloc[-1]
    for k in out:
        v = latest.get(k)
        if pd.notna(v):
            out[k] = float(v)
    latest_year = int(latest["report_date"].year) if pd.notna(latest["report_date"]) else None
    return out, latest_year


def _get_sina_fin(sina: SinaFinancialSource, code: str, latest_year: Optional[int]) -> dict:
    """从新浪三表拿最新年报的财务指标。"""
    out = {"revenue": None, "net_profit": None, "deducted_net_profit": None}
    if latest_year is None:
        return out
    target_date = pd.Timestamp(year=latest_year, month=12, day=31)
    try:
        lrb = sina.get_income_statement(code, num=6)
    except Exception:
        lrb = pd.DataFrame()
    if lrb.empty:
        return out
    lrb = lrb.copy()
    lrb["report_date"] = pd.to_datetime(lrb["report_date"], errors="coerce")
    annual = lrb[lrb["report_date"] == target_date]
    if annual.empty:
        annual = lrb[lrb["report_date"].dt.month == 12]
    if annual.empty:
        return out
    for k in out:
        matched = annual[annual["item_en"] == k]
        if not matched.empty:
            v = matched.iloc[-1].get("value")
            if pd.notna(v):
                out[k] = float(v)
    return out


def _diff_pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """相对差异：|a-b| / max(|a|, |b|)。返回 0~1。None 时返回 None。"""
    if a is None or b is None:
        return None
    if a == 0 and b == 0:
        return 0.0
    denom = max(abs(a), abs(b))
    if denom == 0:
        return None
    return abs(a - b) / denom


def collect_diff(
    codes: list[str], ak: AkShareSource, sina: SinaFinancialSource,
    show_progress: bool = True,
) -> pd.DataFrame:
    """对每只股票收集 AkShare vs 新浪 的差异明细。"""
    rows = []
    iterator = codes
    if show_progress:
        from tqdm import tqdm
        iterator = tqdm(codes, desc="baseline 对照", ncols=80)
    for code in iterator:
        # AkShare 一次拿到指标 + 年份（合并调用，避免拉两次财务接口）
        ak_fin_full, ak_year = _get_akshare_fin(ak, code)

        sina_fin = _get_sina_fin(sina, code, ak_year)
        for field in ("revenue", "net_profit", "deducted_net_profit"):
            a = ak_fin_full.get(field)
            b = sina_fin.get(field)
            diff = _diff_pct(a, b)
            rows.append({
                "code": code,
                "field": field,
                "akshare_value": a,
                "sina_value": b,
                "abs_diff": (abs(a - b) if a is not None and b is not None else None),
                "diff_pct": diff,
                "ak_year": ak_year,
            })
    return pd.DataFrame(rows)


def write_summary(df: pd.DataFrame, out_dir: Path, sample_codes: list[str]) -> None:
    """写 summary.md：差异分布 + 不可替换字段清单。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    md_lines = [
        f"# 数据源 baseline 对照（{time.strftime('%Y-%m-%d %H:%M:%S')}）",
        "",
        f"- 样本股票数: {len(sample_codes)}",
        f"- 对照指标: revenue / net_profit / deducted_net_profit",
        f"- 差异阈值: {DIFF_THRESHOLD_PCT*100:.0f}%（超过则进入『不可替换』清单）",
        "",
        "## 差异分布",
        "",
        "| field | 样本数 | 两源都有 | 中位 diff% | 最大 diff% | 不可替换数 |",
        "|-------|--------|---------|-----------|-----------|-----------|",
    ]
    non_replaceable: dict[str, list[str]] = {}
    for field, group in df.groupby("field"):
        valid = group.dropna(subset=["diff_pct"])
        n_total = len(group)
        n_valid = len(valid)
        if n_valid:
            median = valid["diff_pct"].median() * 100
            max_diff = valid["diff_pct"].max() * 100
            bad = valid[valid["diff_pct"] > DIFF_THRESHOLD_PCT]
            n_bad = len(bad)
            if n_bad:
                non_replaceable[field] = bad["code"].tolist()
        else:
            median = max_diff = float("nan")
            n_bad = 0
        md_lines.append(
            f"| {field} | {n_total} | {n_valid} | "
            f"{median:.2f}% | {max_diff:.2f}% | {n_bad} |"
        )

    md_lines += ["", "## 不可替换字段清单（diff > 5%）", ""]
    if not non_replaceable:
        md_lines.append("所有字段差异均在阈值内，可考虑数据源切换。")
    else:
        md_lines.append("| field | 超阈值股票数 | 股票代码 |")
        md_lines.append("|-------|------------|---------|")
        for field, codes in non_replaceable.items():
            md_lines.append(
                f"| {field} | {len(codes)} | {', '.join(codes[:5])}"
                f"{'...' if len(codes) > 5 else ''} |"
            )

    md_lines += ["", "## 单源缺失（akshare 或 sina 任一为空）", ""]
    miss = df[df["akshare_value"].isna() | df["sina_value"].isna()]
    if miss.empty:
        md_lines.append("无单源缺失。")
    else:
        md_lines.append("| field | akshare 缺失 | sina 缺失 |")
        md_lines.append("|-------|-------------|----------|")
        for field, group in miss.groupby("field"):
            ak_miss = group["akshare_value"].isna().sum()
            sina_miss = group["sina_value"].isna().sum()
            md_lines.append(f"| {field} | {ak_miss} | {sina_miss} |")

    (out_dir / "summary.md").write_text("\n".join(md_lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--codes", nargs="*", default=None,
                        help="手动指定股票代码（默认从候选池抽样）")
    parser.add_argument("--sample-size", type=int, default=30,
                        help="抽样股票数（默认 30）")
    parser.add_argument("--output-dir", type=Path,
                        default=config.EXPORTS_DIR / "baselines")
    args = parser.parse_args()

    from src.storage import DuckDBStore
    store = DuckDBStore()
    try:
        if args.codes:
            codes = [str(c).zfill(6) for c in args.codes]
        else:
            codes = _sample_codes(store, args.sample_size)
            if not codes:
                print("✗ 候选池为空，请先跑 bootstrap")
                return 1
        print(f"✓ 样本 {len(codes)} 只: {codes[:5]}{'...' if len(codes)>5 else ''}")

        run_dir = args.output_dir / f"baseline_{time.strftime('%Y%m%d')}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "samples.json").write_text(
            json.dumps({"codes": codes, "created_at": time.strftime('%Y-%m-%d %H:%M:%S')},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        ak = AkShareSource()
        try:
            sina = SinaFinancialSource()
        except Exception as e:
            print(f"✗ SinaFinancialSource 初始化失败: {e}")
            return 1

        df = collect_diff(codes, ak, sina)
        df.to_csv(run_dir / "raw_diff.csv", index=False, encoding="utf-8-sig")
        write_summary(df, run_dir, codes)

        print(f"\n✓ 输出: {run_dir}")
        print(f"  raw_diff.csv ({len(df)} 行)")
        print(f"  summary.md")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
