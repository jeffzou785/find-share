"""研报入库 + PDF 下载 + RAG 集成一站式脚本。

流程：
1. 从东财拉研报列表 → 入库 broker_reports
2. （可选）从同花顺拉一致预期 → 入库 eps_forecast_consensus
3. 对未下载的研报批量下载 PDF → 回填 broker_reports.pdf_path
4. 对未入 RAG 的 PDF 调 ResearchRAG.ingest_pdf → 回填 ingested_to_rag=True

用法：
    python scripts/import_research_reports.py 600519                  # 单只股票，默认拉 1 页研报
    python scripts/import_research_reports.py 600519 000858 --max-pages 3
    python scripts/import_research_reports.py 600519 --skip-pdf       # 只拉研报列表，不下 PDF
    python scripts/import_research_reports.py 600519 --skip-thes --skip-pdf --skip-rag  # 仅东财列表入库
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors.eastmoney_research import EastMoneyResearchSource
from src.collectors.ths_forecast import ThsForecastSource
from src.knowledge.research_rag import ResearchRAG
from src.storage import DuckDBStore


def ingest_one_stock(
    code: str,
    max_pages: int,
    skip_ths: bool,
    skip_pdf: bool,
    skip_rag: bool,
    max_pdfs: int,
    store: DuckDBStore,
) -> dict:
    """处理单只股票。返回各步骤统计。"""
    stats = {"reports_fetched": 0, "reports_saved": 0, "pdf_downloaded": 0, "rag_ingested": 0}

    # Step 1: 东财研报列表
    em_src = EastMoneyResearchSource()
    df = em_src.get_reports(code, max_pages=max_pages)
    stats["reports_fetched"] = len(df)
    if not df.empty:
        n = store.save_broker_reports(df)
        stats["reports_saved"] = n
        print(f"  [1/4] 东财研报入库: {n} 篇")
    else:
        print(f"  [1/4] 东财返回空")
        return stats

    # Step 2: 同花顺一致预期
    if not skip_ths:
        try:
            ths = ThsForecastSource()
            ths_df = ths.get_eps_forecast(code)
            n = store.save_eps_forecast_consensus(ths_df)
            print(f"  [2/4] 同花顺一致预期入库: {n} 行")
        except Exception as e:
            print(f"  [2/4] 同花顺失败（不影响主流程）: {type(e).__name__}: {str(e)[:80]}")
    else:
        print(f"  [2/4] 同花顺跳过")

    # Step 3: PDF 下载
    if not skip_pdf:
        pending = store.load_broker_reports(code=code, need_pdf=False)
        if max_pdfs > 0:
            pending = pending.head(max_pdfs)
        print(f"  [3/4] 待下载 PDF: {len(pending)} 份")
        for _, rec in pending.iterrows():
            try:
                pdf_path = em_src.download_pdf(rec)
                if pdf_path:
                    store.update_broker_report_pdf_path(rec["report_id"], str(pdf_path))
                    stats["pdf_downloaded"] += 1
                    # Step 4: 入 RAG
                    if not skip_rag:
                        rag = getattr(store, "_rag", None)
                        if rag is None:
                            rag = ResearchRAG()
                            store._rag = rag
                        metadata = {
                            "stock_code": rec["code"],
                            "stock_name": rec.get("stock_name", ""),
                            "broker": rec["broker"],
                            "report_date": str(rec.get("publish_date", "")),
                        }
                        chunks = rag.ingest_pdf(pdf_path, metadata=metadata)
                        if chunks > 0:
                            store.mark_broker_report_ingested(rec["report_id"])
                            stats["rag_ingested"] += chunks
                            print(f"    ✓ {pdf_path.name} ({chunks} chunks)")
                        else:
                            print(f"    ✓ {pdf_path.name} (已索引)")
            except Exception as e:
                print(f"    ✗ {rec.get('publish_date', '')} {rec.get('broker')}: {type(e).__name__}: {str(e)[:60]}")
            time.sleep(0.5)
    else:
        print(f"  [3/4] PDF 下载跳过")

    print(f"  [4/4] RAG ingest: {stats['rag_ingested']} chunks")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="研报入库 + PDF + RAG 一站式")
    parser.add_argument("codes", nargs="+", help="股票代码（如 600519 000858）")
    parser.add_argument("--max-pages", type=int, default=1, help="东财研报拉取页数（默认 1 页=100篇）")
    parser.add_argument("--skip-ths", action="store_true", help="跳过同花顺一致预期")
    parser.add_argument("--skip-pdf", action="store_true", help="跳过 PDF 下载")
    parser.add_argument("--skip-rag", action="store_true", help="跳过 RAG ingest")
    parser.add_argument("--max-pdfs", type=int, default=5, help="单只股票最多下载几份 PDF（默认 5）")
    args = parser.parse_args()

    codes = [c.zfill(6) for c in args.codes]
    store = DuckDBStore()

    total_stats = {"reports_fetched": 0, "reports_saved": 0, "pdf_downloaded": 0, "rag_ingested": 0}

    for code in codes:
        print(f"\n{'=' * 60}")
        print(f"处理 {code}")
        print(f"{'=' * 60}")
        stats = ingest_one_stock(
            code=code,
            max_pages=args.max_pages,
            skip_ths=args.skip_ths,
            skip_pdf=args.skip_pdf,
            skip_rag=args.skip_rag,
            max_pdfs=args.max_pdfs,
            store=store,
        )
        for k, v in stats.items():
            total_stats[k] += v

    print(f"\n{'=' * 60}")
    print(f"汇总")
    print(f"{'=' * 60}")
    print(f"  研报拉取: {total_stats['reports_fetched']} 篇")
    print(f"  研报入库: {total_stats['reports_saved']} 篇")
    print(f"  PDF 下载: {total_stats['pdf_downloaded']} 份")
    print(f"  RAG chunks: {total_stats['rag_ingested']} 块")

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
