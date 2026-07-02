"""研报 RAG CLI（jieba + TF-IDF 版本）。

用法：
    python scripts/research_rag_cli.py index                    # 索引所有研报
    python scripts/research_rag_cli.py search "海外业务增速"     # 语义检索
    python scripts/research_rag_cli.py search --stock 600031 "海外业务"
    python scripts/research_rag_cli.py info                     # 查看 RAG 状态
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import config
from src.knowledge import ResearchRAG


def cmd_index(args) -> int:
    rag = ResearchRAG()
    print(f"现有 chunks: {rag.count}")
    pdf_dir = Path(args.pdf_dir) if args.pdf_dir else config.RESEARCH_REPORT_DIR
    print(f"索引目录: {pdf_dir}")
    if not pdf_dir.exists():
        print(f"✗ 目录不存在")
        return 1
    n = rag.index_directory(pdf_dir, force_refresh=args.force)
    print(f"\n✓ 新增 {n} chunks，总计 {rag.count} chunks")
    return 0


def cmd_search(args) -> int:
    rag = ResearchRAG()
    if rag.count == 0:
        print("✗ 向量库为空，先运行 `python scripts/research_rag_cli.py index`")
        return 1

    print(f"查询: {args.query}")
    if args.stock:
        print(f"过滤: 股票={args.stock}")
    if args.broker:
        print(f"过滤: 券商={args.broker}")
    print()

    results = rag.query(
        args.query,
        stock_code=args.stock,
        broker=args.broker,
        top_k=args.top_k,
    )

    if not results:
        print("✗ 无匹配结果")
        return 1

    for i, r in enumerate(results, 1):
        print(
            f"--- [{i}] 相似度 {r.score:.3f} | {r.stock_code} {r.stock_name} "
            f"| {r.broker} | {r.report_date} | P{r.page} ---"
        )
        if r.section_title:
            print(f"章节: {r.section_title}")
        text = r.text.replace("\n", " ").strip()
        print(text[:500] + ("..." if len(text) > 500 else ""))
        print()
    return 0


def cmd_info(args) -> int:
    rag = ResearchRAG()
    stats = rag.get_stats()
    print(f"RAG 状态:")
    print(f"  持久化目录: {rag.persist_dir}")
    print(f"  总 chunks: {stats['total_chunks']}")
    print(f"  PDF 数: {stats['total_pdfs']}")
    print(f"  内容 hash 数: {stats.get('total_content_hashes', 0)}")
    if stats["by_stock"]:
        print(f"\n按股票分布（前 20）:")
        for code, cnt in stats["by_stock"]:
            print(f"  {code}: {cnt} chunks")
    if stats["by_broker"]:
        print(f"\n按券商分布:")
        for broker, cnt in stats["by_broker"]:
            print(f"  {broker}: {cnt} chunks")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="研报 RAG CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="索引研报目录")
    p_index.add_argument("--pdf-dir", help="PDF 目录（默认 research_reports/）")
    p_index.add_argument("--force", action="store_true", help="强制重新索引")

    p_search = sub.add_parser("search", help="TF-IDF 语义检索")
    p_search.add_argument("query", help="查询文本")
    p_search.add_argument("--stock", help="过滤股票代码")
    p_search.add_argument("--broker", help="过滤券商")
    p_search.add_argument("--top-k", type=int, default=8)

    sub.add_parser("info", help="查看 RAG 状态")

    args = parser.parse_args()

    if args.cmd == "index":
        return cmd_index(args)
    elif args.cmd == "search":
        return cmd_search(args)
    elif args.cmd == "info":
        return cmd_info(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
