"""全市场行业映射补全：用东财 F10（emweb）拉所有股票的精确行业。

EM2016 三级分类 + CSRC 证监会门类（用于剔除金融股）。
结果存 DuckDB 的 stock_industry 表（覆盖原 Sina 行业映射）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from src.collectors import AkShareSource
from src.collectors.emweb_client import EmWebClient
from src.storage import DuckDBStore


def main() -> int:
    print("=" * 70)
    print("全市场行业映射补全（东财 F10 emweb）")
    print("=" * 70)

    source = AkShareSource()
    store = DuckDBStore()

    try:
        # 1. 拉全市场股票列表
        print("\n[1/3] 加载股票列表...")
        stocks = store.load_stocks()
        if stocks.empty:
            stocks = source.get_stock_list()
            store.save_stocks(stocks)
        codes = stocks["code"].tolist()
        print(f"  ✓ {len(codes)} 只股票")

        # 2. 跑 emweb 全市场
        print(f"\n[2/3] 调东财 F10 拉行业（8 并发，约 3-5 分钟）...")
        client = EmWebClient(max_workers=8, rate_limit_per_sec=0)
        df = client.get_company_info_batch(codes, show_progress=True)
        if df.empty:
            print("  ✗ 全部失败")
            return 1
        print(f"\n  ✓ 成功 {len(df)} / {len(codes)} 只 ({len(df) / len(codes) * 100:.1f}%)")

        # 3. 写 DuckDB（保留 sina 行业映射的字段，但 sw_first 改用 EM2016 一级）
        print("\n[3/3] 写 DuckDB stock_industry 表...")
        df = df.rename(
            columns={
                "em2016_first": "sw_first",
                "em2016_second": "sw_second",
                "em2016": "em2016",
                "csrc": "csrc_industry",
                "csrc_section": "csrc_section",
                "province": "province",
            }
        )
        # sina_industry 留空（不再用新浪行业）
        if "sina_industry" not in df.columns:
            df["sina_industry"] = None
        # 其他列补默认值（兼容旧表 schema）
        for col in ["pe_ttm", "pb", "total_mktcap_wan", "float_mktcap_wan", "turnover_ratio"]:
            if col not in df.columns:
                df[col] = None
        df["updated_at"] = pd.Timestamp.now()

        # 只保留 stock_industry 表的列
        keep_cols = [
            "code", "name", "sina_industry", "sw_first", "sw_second",
            "em2016", "csrc_industry", "csrc_section", "province",
            "pe_ttm", "pb", "total_mktcap_wan", "float_mktcap_wan",
            "turnover_ratio", "updated_at",
        ]
        df = df[[c for c in keep_cols if c in df.columns]]

        # 先 ALTER TABLE 加新列，再 upsert
        _ensure_columns(store.conn)

        # 截断 + 重写（一次性更新整个行业映射）
        store.conn.execute("DELETE FROM stock_industry")
        store.conn.register("_ind", df)
        cols = ", ".join(df.columns)
        store.conn.execute(f"INSERT INTO stock_industry ({cols}) SELECT {cols} FROM _ind")
        store.conn.unregister("_ind")
        print(f"  ✓ 已写入 {len(df)} 行")

        # 行业分布
        print("\n  EM2016 一级行业分布（前 20）:")
        dist = df["sw_first"].value_counts().head(20)
        for ind, cnt in dist.items():
            print(f"    {ind:<15} {cnt}")

        # CSRC 门类分布
        print("\n  CSRC 门类分布:")
        csrc_dist = df["csrc_section"].value_counts()
        for section, cnt in csrc_dist.items():
            print(f"    {section:<15} {cnt}")

        return 0

    except Exception as e:
        print(f"\n✗ 失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        store.close()


def _ensure_columns(conn) -> None:
    """确保 stock_industry 表有所有新列（ALTER TABLE IF NOT EXISTS 不支持，需 try）"""
    new_cols = {
        "sw_second": "VARCHAR",
        "em2016": "VARCHAR",
        "csrc_industry": "VARCHAR",
        "csrc_section": "VARCHAR",
        "province": "VARCHAR",
    }
    for col, dtype in new_cols.items():
        try:
            conn.execute(f"ALTER TABLE stock_industry ADD COLUMN {col} {dtype}")
        except Exception:
            pass  # 列已存在


if __name__ == "__main__":
    sys.exit(main())
