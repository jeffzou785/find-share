"""初始化基础数据：股票列表 + 行业映射 + 申万行业。

一次性把"全市场静态数据"灌进 DuckDB，后续策略只读 DuckDB。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors import AkShareSource
from src.storage import DuckDBStore


def main() -> int:
    print("=" * 70)
    print("初始化基础数据")
    print("=" * 70)

    source = AkShareSource()
    store = DuckDBStore()

    try:
        # 1. 股票列表
        print("\n[1/4] 全市场股票列表...")
        stocks = source.get_stock_list()
        store.save_stocks(stocks)
        print(f"  ✓ {len(stocks)} 只股票入库")

        # 2. 申万一级行业
        print("\n[2/4] 申万一级行业...")
        sw_first = source.get_sw_first_industry()
        # 直接 upsert
        sw_first = sw_first.rename(
            columns={
                "成份个数": "constituent_count",
                "TTM(滚动)市盈率": "pe_ttm",
                "市净率": "pb",
            }
        )
        sw_first["updated_at"] = pd.Timestamp.now()
        store.upert_dataframe("industry_first", sw_first)
        print(f"  ✓ {len(sw_first)} 个一级行业入库")

        # 3. 申万二级行业
        print("\n[3/4] 申万二级行业...")
        sw_second = source.get_sw_second_industry()
        sw_second["updated_at"] = pd.Timestamp.now()
        store.upert_dataframe("industry_second", sw_second)
        print(f"  ✓ {len(sw_second)} 个二级行业入库")

        # 4. 股票 → 行业映射（含 PE/PB 快照）
        print("\n[4/4] 全市场股票行业映射（通过 Sina 行业，需逐板块拉成分股）...")
        mapping = source.get_stock_industry_mapping()
        store.save_stock_industry(mapping)
        print(f"  ✓ {len(mapping)} 只股票带行业映射入库")

        # 行业分布
        if "sw_first" in mapping.columns:
            print("\n  行业分布（按申万一级 fallback）:")
            dist = mapping["sw_first"].value_counts().head(15)
            for ind, cnt in dist.items():
                print(f"    {ind:<15} {cnt}")

        return 0

    except Exception as e:
        print(f"\n✗ 失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
