"""统一 CLI 测试（P2-0）。

只测子命令路由和 list 输出，不实际跑业务逻辑。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _run_cli(*args: str, timeout: int = 10) -> tuple[int, str, str]:
    """运行 `python -m src.pipeline.cli <args>`，返回 (returncode, stdout, stderr)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "src.pipeline.cli", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestCliRouting:
    def test_list_shows_all_subcommands(self):
        rc, out, _ = _run_cli("list")
        assert rc == 0
        # 所有 subcommand 都列出
        for cmd in ("bootstrap", "refresh", "screen", "strategy1",
                    "strategy3", "reports", "pdf", "rag", "baseline",
                    "backtest", "monitor", "p0-audit", "label-export", "label-import",
                    "pharma-vbp", "pharma-gt"):
            assert cmd in out

    def test_unknown_command_errors(self):
        rc, _, err = _run_cli("nonexistent")
        assert rc != 0

    def test_help_for_subcommand(self):
        """refresh --help 应该透传到 refresh 脚本。"""
        rc, out, _ = _run_cli("refresh", "--help")
        assert rc == 0
        assert "--codes" in out or "--limit" in out

    def test_screen_help_passthrough(self):
        rc, out, _ = _run_cli("screen", "--help")
        assert rc == 0
        assert "--period" in out
        assert "--strategy" in out
