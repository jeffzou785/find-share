"""PDF 命名回归测试（P0-1）。

确保年报 canonical 命名为 _annual_report.pdf，并兼容 legacy _annual.pdf。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.collectors.cninfo_downloader import (
    CnInfoDownloader,
    build_pdf_filename,
)


class TestBuildPdfFilename:
    def test_annual_uses_report_suffix(self):
        assert build_pdf_filename("600031", 2024, "annual") == "600031_2024_annual_report.pdf"

    def test_half_year_uses_type_suffix(self):
        assert build_pdf_filename("600031", 2024, "half_year") == "600031_2024_half_year.pdf"

    def test_q1_uses_type_suffix(self):
        assert build_pdf_filename("000001", 2024, "q1") == "000001_2024_q1.pdf"

    def test_q3_uses_type_suffix(self):
        assert build_pdf_filename("000001", 2024, "q3") == "000001_2024_q3.pdf"

    def test_code_zero_padded(self):
        assert build_pdf_filename("31", 2024, "annual") == "000031_2024_annual_report.pdf"


class TestDownloadReportSkipLogic:
    """download_report 在 skip_if_exists=True 时应识别 canonical 和 legacy 命名。"""

    def _make_downloader(self, tmp_path: Path) -> CnInfoDownloader:
        """构造 Downloader 但不触发任何网络请求。"""
        dl = object.__new__(CnInfoDownloader)
        dl.cache_dir = tmp_path / "cache"
        dl.cache_dir.mkdir(parents=True, exist_ok=True)
        dl._orgid_cache = {}
        return dl

    def _write_dummy_pdf(self, path: Path, size_kb: int = 200) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4" + b"0" * (size_kb * 1024))

    def test_canonical_exists_skips_download(self, tmp_path: Path, monkeypatch):
        """canonical 命名存在时不应调 query_report。"""
        save_dir = tmp_path / "pdfs"
        canonical = save_dir / "600031_2024_annual_report.pdf"
        self._write_dummy_pdf(canonical)

        dl = self._make_downloader(tmp_path)

        def _fail(*args, **kwargs):
            raise AssertionError("query_report 不应被调用")

        monkeypatch.setattr(dl, "query_report", _fail)
        result = dl.download_report("600031", 2024, "annual", save_dir=save_dir)
        assert result == canonical

    def test_legacy_annual_skips_download(self, tmp_path: Path, monkeypatch):
        """legacy _annual.pdf 存在时应被识别，不重新下载。"""
        save_dir = tmp_path / "pdfs"
        legacy = save_dir / "600031_2024_annual.pdf"
        self._write_dummy_pdf(legacy)

        dl = self._make_downloader(tmp_path)

        def _fail(*args, **kwargs):
            raise AssertionError("query_report 不应被调用")

        monkeypatch.setattr(dl, "query_report", _fail)
        result = dl.download_report("600031", 2024, "annual", save_dir=save_dir)
        assert result == legacy

    def test_legacy_not_used_for_half_year(self, tmp_path: Path, monkeypatch):
        """legacy 兼容只对年报生效，半年报不存在 _half_year_report 这种命名。"""
        save_dir = tmp_path / "pdfs"
        # 放一个 _half_year_report.pdf 不应被识别
        weird = save_dir / "600031_2024_half_year_report.pdf"
        self._write_dummy_pdf(weird)

        dl = self._make_downloader(tmp_path)
        called = {"count": 0}

        def _fake_query(*args, **kwargs):
            called["count"] += 1
            raise FileNotFoundError("simulated not found")

        monkeypatch.setattr(dl, "query_report", _fake_query)
        with pytest.raises(FileNotFoundError):
            dl.download_report("600031", 2024, "half_year", save_dir=save_dir)
        assert called["count"] == 1

    def test_small_file_not_recognized(self, tmp_path: Path, monkeypatch):
        """小于 100KB 的文件视为损坏，应重新查询。"""
        save_dir = tmp_path / "pdfs"
        tiny = save_dir / "600031_2024_annual_report.pdf"
        self._write_dummy_pdf(tiny, size_kb=50)  # 50KB < 100KB

        dl = self._make_downloader(tmp_path)
        called = {"count": 0}

        def _fake_query(*args, **kwargs):
            called["count"] += 1
            raise FileNotFoundError("simulated not found")

        monkeypatch.setattr(dl, "query_report", _fake_query)
        with pytest.raises(FileNotFoundError):
            dl.download_report("600031", 2024, "annual", save_dir=save_dir)
        assert called["count"] == 1
