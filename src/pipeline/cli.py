"""P2-0：统一 CLI 入口。

把现有 11 个独立脚本封装为统一命令，新用户只需记住 `python -m src.pipeline.cli`。
旧脚本继续保留，CLI 只是 thin wrapper（通过 subprocess 调用），不复制业务逻辑。

子命令：
- bootstrap     初始化股票列表 + 行业映射（首次运行）
- refresh       预热 PE/PB 历史 + 财务摘要到本地 DuckDB（P1.5-1）
- screen        财报披露后筛选主入口（run_after_disclosure.py）
- strategy1     策略一：消费反转（run_phase2_strategy1.py）
- strategy3     策略三：出海隐形冠军（run_phase3_strategy3.py）
- reports       拉研报 + 一致预期 + RAG ingest（import_research_reports.py）
- pdf           下载定期报告 PDF（download_annual_reports.py）
- rag           研报 RAG 检索（research_rag_cli.py）
- baseline      数据源对照（data_source_baseline.py）

用法示例：
    python3 -m src.pipeline.cli bootstrap
    python3 -m src.pipeline.cli refresh --limit 30
    python3 -m src.pipeline.cli screen --period 2025A --strategy all --limit 30
    python3 -m src.pipeline.cli reports 600519 --max-pdfs 3
    python3 -m src.pipeline.cli rag search "海外订单" --stock 600519
"""
from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


# 子命令 → 对应脚本的映射
SUBCOMMAND_SCRIPTS: dict[str, str] = {
    "bootstrap": str(PROJECT_ROOT / "src" / "pipeline" / "bootstrap.py"),
    "bootstrap-industry": str(SCRIPTS_DIR / "bootstrap_emweb_industry.py"),
    "refresh": str(SCRIPTS_DIR / "refresh_financials_and_valuation.py"),
    "screen": str(SCRIPTS_DIR / "run_after_disclosure.py"),
    "strategy1": str(SCRIPTS_DIR / "run_phase2_strategy1.py"),
    "strategy3": str(SCRIPTS_DIR / "run_phase3_strategy3.py"),
    "reports": str(SCRIPTS_DIR / "import_research_reports.py"),
    "pdf": str(SCRIPTS_DIR / "download_annual_reports.py"),
    "rag": str(SCRIPTS_DIR / "research_rag_cli.py"),
    "baseline": str(SCRIPTS_DIR / "data_source_baseline.py"),
    "baseline-diff": str(SCRIPTS_DIR / "baseline_diff.py"),
    "import-overseas": str(SCRIPTS_DIR / "import_overseas_revenue.py"),
}


def _run_script(script_path: str, extra_args: list[str]) -> int:
    """通过 runpy 执行脚本，把 extra_args 注入 sys.argv。

    等价于 `python3 <script> <extra_args>`，但避免子进程开销和 stdin/stdout 解析。
    """
    sys.argv = [script_path] + extra_args
    try:
        runpy.run_path(script_path, run_name="__main__")
        return 0
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.pipeline.cli",
        description="find-share 统一 CLI（封装 11 个独立脚本）",
        epilog="各子命令的详细参数请用 `python -m src.pipeline.cli <cmd> --help` 查看",
    )
    parser.add_argument(
        "command",
        choices=list(SUBCOMMAND_SCRIPTS.keys()) + ["list"],
        help="子命令",
    )
    parser.add_argument(
        "args", nargs=argparse.REMAINDER,
        help="传给子命令的参数",
    )
    opts = parser.parse_args()

    if opts.command == "list":
        print("可用子命令：")
        for cmd, script in SUBCOMMAND_SCRIPTS.items():
            print(f"  {cmd:<20} → {Path(script).name}")
        return 0

    script = SUBCOMMAND_SCRIPTS[opts.command]
    return _run_script(script, opts.args)


if __name__ == "__main__":
    sys.exit(main())
