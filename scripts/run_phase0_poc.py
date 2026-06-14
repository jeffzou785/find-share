"""Phase 0 POC：验证能否从年报 PDF 稳定提取海外收入数据。

流程：
1. 下载 10 家典型出海公司的 2024 年报 PDF（cninfo）
2. 用 pdfplumber 解析"分地区营业收入"附注
3. 提取"境外/海外/出口"等关键词对应的收入
4. 统计成功率，输出 JSON + Markdown 报告
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# 加入项目根
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors.annual_report_parser import (
    ParseResult,
    parse_annual_report,
    summarize_results,
)
from src.collectors.cninfo_downloader import CnInfoDownloader

# 10 家典型出海公司（机械 / 汽车 / 化工 / 家电）
POC_STOCKS = [
    ("600031", "三一重工"),
    ("600660", "福耀玻璃"),
    ("601966", "玲珑轮胎"),
    ("603337", "杰克股份"),
    ("002594", "比亚迪"),
    ("600690", "海尔智家"),
    ("000921", "海信家电"),
    ("000157", "中联重科"),
    ("000338", "潍柴动力"),
    ("300751", "迈为股份"),
]


def main() -> int:
    print("=" * 70)
    print("Phase 0 POC: 年报附注海外收入提取")
    print("=" * 70)

    downloader = CnInfoDownloader()
    results: list[ParseResult] = []
    extracted_records = []

    for code, name in POC_STOCKS:
        print(f"\n>>> {code} {name}")
        try:
            pdf_path = downloader.download_annual_report(code, year=2024)
            print(f"  下载完成: {pdf_path.name} ({pdf_path.stat().st_size // 1024} KB)")

            result = parse_annual_report(pdf_path, stock_code=code)
            print(
                f"  解析: {'✓ 成功' if result.success else '✗ ' + result.error}"
            )
            if result.success:
                for r in result.records:
                    print(
                        f"    - {r.region_name}: {r.revenue:,.2f} {r.revenue_unit} (P{r.source_page})"
                    )
                    extracted_records.append(
                        {
                            "stock_code": code,
                            "stock_name": name,
                            "region_name": r.region_name,
                            "revenue": r.revenue,
                            "revenue_unit": r.revenue_unit,
                            "source_page": r.source_page,
                        }
                    )
            results.append(result)
        except Exception as e:
            print(f"  错误: {type(e).__name__}: {e}")
            results.append(
                ParseResult(
                    stock_code=code,
                    pdf_path="",
                    success=False,
                    error=f"{type(e).__name__}: {e}",
                )
            )

        time.sleep(1.5)  # 友善限速

    # 汇总
    summary = summarize_results(results)
    print("\n" + "=" * 70)
    print("POC 结果汇总")
    print("=" * 70)
    print(f"  总数: {summary['total']}")
    print(f"  成功: {summary['success']}")
    print(f"  失败: {summary['failed']}")
    print(f"  成功率: {summary['success_rate']:.1f}%")

    if summary["failed"]:
        print("\n  失败详情:")
        for f in summary["failures"]:
            print(f"    - {f['stock_code']}: {f['error']}")

    # 保存
    export_dir = PROJECT_ROOT / "data" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    json_path = export_dir / "phase0_poc_result.json"
    md_path = export_dir / "phase0_poc_report.md"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "summary": summary,
                "extracted_records": extracted_records,
                "failures": summary["failures"],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    _write_markdown_report(md_path, summary, extracted_records, results)
    print(f"\n报告已生成:\n  - {json_path}\n  - {md_path}")

    # 通过标准：成功率 >= 80%
    if summary["success_rate"] >= 80:
        print(f"\n✓ POC 通过（>=80%），可推进 Phase 1-2")
        return 0
    elif summary["success_rate"] >= 50:
        print(f"\n⚠ POC 部分通过（50-80%），需要优化解析器或增加数据源")
        return 1
    else:
        print(f"\n✗ POC 失败（<50%），策略三数据基础不成立，需要降级方案")
        return 2


def _write_markdown_report(
    md_path: Path,
    summary: dict,
    records: list[dict],
    results: list[ParseResult],
) -> None:
    lines = [
        "# Phase 0 POC 报告：年报附注海外收入提取",
        "",
        f"- **测试日期**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **测试样本**: {summary['total']} 家典型出海公司",
        f"- **成功率**: {summary['success_rate']:.1f}% ({summary['success']}/{summary['total']})",
        "",
        "## 通过标准",
        "",
        "| 阈值 | 结论 |",
        "|------|------|",
        f"| ≥ 80% | ✅ 推进 Phase 1-2 |",
        f"| 50-80% | ⚠️ 需优化解析器 |",
        f"| < 50% | ❌ 策略三降级 |",
        "",
        f"**本次结果**: {_verdict(summary['success_rate'])}",
        "",
        "## 提取的海外收入记录",
        "",
        "| 股票代码 | 公司 | 地区关键词 | 金额 | 单位 | 页码 |",
        "|----------|------|-----------|------|------|------|",
    ]
    for r in records:
        lines.append(
            f"| {r['stock_code']} | {r['stock_name']} | {r['region_name']} | "
            f"{r['revenue']:,.2f} | {r['revenue_unit']} | P{r['source_page']} |"
        )
    lines.append("")

    if summary["failures"]:
        lines += ["## 失败详情", "", "| 股票代码 | 错误 |", "|---------|------|"]
        for f in summary["failures"]:
            lines.append(f"| {f['stock_code']} | {f['error']} |")
        lines.append("")

    lines += [
        "## 解析路径",
        "",
        "1. 用 `pdfplumber` 从 PDF 后 1/3 扫描含「分地区/分区域」关键词的页面",
        "2. 同时校验页面包含「营业收入/主营业务」",
        "3. 对每个表格行用关键词匹配「境外/海外/出口」",
        "4. 用正则提取行内金额，自动识别亿元/万元/元单位",
        "5. 同页同时尝试表格提取 + 纯文本兜底",
        "",
        "## 下一步",
        "",
        "- 若成功率 ≥80%：推进 Phase 1-2",
        "- 若 50-80%：在解析器中增加年报附注的常见变体（如中英文混排、合并表格）",
        "- 若 <50%：策略三降级，改为「主营构成（东财）+ 研报 LLM 抽取」双源",
    ]

    md_path.write_text("\n".join(lines), encoding="utf-8")


def _verdict(rate: float) -> str:
    if rate >= 80:
        return "✅ 通过（≥80%），可推进 Phase 1-2"
    if rate >= 50:
        return "⚠️ 部分通过（50-80%），需优化"
    return "❌ 失败（<50%），需降级"


if __name__ == "__main__":
    sys.exit(main())
