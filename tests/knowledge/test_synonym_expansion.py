"""RAG 同义词扩展测试（P1.5-5）。

验证：
- expand_query_synonyms 正确扩展
- 同义词查询的检索召回率提升
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.knowledge.research_rag import (
    ResearchChunk,
    ResearchRAG,
    expand_query_synonyms,
    infer_section_title,
    SYNONYM_GROUPS,
)


class TestExpandQuerySynonyms:
    def test_overseas_terms_expand(self):
        tokens = ["海外"]
        expanded = expand_query_synonyms(tokens)
        assert "海外" in expanded
        assert "境外" in expanded
        assert "国外" in expanded
        assert "出口" in expanded
        assert "国际" in expanded

    def test_order_terms_expand(self):
        tokens = ["订单"]
        expanded = expand_query_synonyms(tokens)
        assert "订单" in expanded
        assert "中标" in expanded
        assert "签约" in expanded

    def test_no_synonyms_returns_original(self):
        tokens = ["茅台"]
        expanded = expand_query_synonyms(tokens)
        assert expanded == ["茅台"]

    def test_empty_tokens(self):
        assert expand_query_synonyms([]) == []

    def test_dedup(self):
        """同义词组内多个词同时出现时，扩展结果去重。"""
        tokens = ["海外", "境外"]
        expanded = expand_query_synonyms(tokens)
        # 不应有重复
        assert len(expanded) == len(set(expanded))
        # 应包含全部同义词
        for syn in ("海外", "境外", "国外", "出口", "国际", "外销"):
            assert syn in expanded

    def test_synonym_groups_non_empty(self):
        assert len(SYNONYM_GROUPS) > 0
        # 每组至少 2 个词
        for grp in SYNONYM_GROUPS:
            assert len(grp) >= 2


class TestSynonymRetrieval:
    """端到端测试：同义词扩展提升召回率。"""

    @pytest.fixture
    def rag(self, tmp_path: Path):
        return ResearchRAG(persist_dir=tmp_path / "rag")

    def test_query_overseas_matches_synonym_in_doc(self, rag: ResearchRAG):
        """查询"海外订单"，能召回只含"境外"的 chunk。"""
        # 模拟一个 chunk 只含"境外"，不含"海外"
        chunks_text = [
            "公司境外业务持续增长，新签订单 50 亿元",
            "国内市场稳定，无海外业务",
        ]
        # 用 _add_chunks 直接入库（绕开 PDF 解析）
        rag._add_chunks(
            "test.pdf",
            [
                ResearchChunk(
                    stock_code="600031", stock_name="X", broker="b",
                    report_date="2025-01-01", page=1, text=t,
                )
                for t in chunks_text
            ],
        )
        rag._build_index()

        # 查询"海外订单"（不含"境外"原文）
        results = rag.query("海外订单", top_k=5)
        assert len(results) > 0
        # 应该召回第一条 chunk（含"境外订单"）
        top = results[0]
        assert "境外" in top.text

    def test_query_returns_section_title(self, rag: ResearchRAG):
        rag._add_chunks(
            "section.pdf",
            [
                ResearchChunk(
                    stock_code="600031", stock_name="X", broker="b",
                    report_date="2025-01-01", page=3,
                    section_title="海外业务",
                    text="海外业务\n公司出口订单保持较快增长。",
                )
            ],
        )
        rag._build_index()
        results = rag.query("出口订单", top_k=1)
        assert len(results) == 1
        assert results[0].section_title == "海外业务"


class TestResearchRagDedup:
    def test_infer_section_title(self):
        assert infer_section_title("一、海外业务\n公司境外收入增长") == "海外业务"
        assert infer_section_title("1.1海外业务\n公司境外收入增长") == "海外业务"
        assert infer_section_title("风险提示\n汇率波动风险") == "风险提示"

    def test_ingest_pdf_skips_same_content_hash(self, tmp_path: Path, monkeypatch):
        import src.knowledge.research_rag as rr

        rag = ResearchRAG(persist_dir=tmp_path / "rag")

        def fake_parse(_path):
            return [
                ResearchChunk(
                    stock_code="600031", stock_name="X", broker="b",
                    report_date="2025-01-01", page=1,
                    section_title="核心观点",
                    text="核心观点\n公司海外订单增长，出口收入提升。",
                )
            ]

        monkeypatch.setattr(rr, "parse_pdf_to_chunks", fake_parse)
        assert rag.ingest_pdf(tmp_path / "a.pdf") == 1
        assert rag.ingest_pdf(tmp_path / "b.pdf") == 0
        stats = rag.get_stats()
        assert stats["total_chunks"] == 1
        assert stats["total_pdfs"] == 1
        assert stats["total_content_hashes"] == 1

    def test_shared_boilerplate_does_not_skip_different_reports(self, tmp_path: Path, monkeypatch):
        import src.knowledge.research_rag as rr

        rag = ResearchRAG(persist_dir=tmp_path / "rag")

        def fake_parse(path):
            suffix = Path(path).stem
            return [
                ResearchChunk(
                    stock_code="600031", stock_name="X", broker="b",
                    report_date="2025-01-01", page=1,
                    text="风险提示\n汇率波动、原材料价格波动。",
                ),
                ResearchChunk(
                    stock_code="600031", stock_name="X", broker="b",
                    report_date="2025-01-01", page=2,
                    text=f"核心观点\n{suffix} 公司海外订单增长。",
                ),
            ]

        monkeypatch.setattr(rr, "parse_pdf_to_chunks", fake_parse)
        assert rag.ingest_pdf(tmp_path / "a.pdf") == 2
        assert rag.ingest_pdf(tmp_path / "b.pdf") == 2
        stats = rag.get_stats()
        assert stats["total_chunks"] == 4
        assert stats["total_pdfs"] == 2
        assert stats["total_content_hashes"] == 2

    def test_existing_db_backfills_content_hashes(self, tmp_path: Path, monkeypatch):
        import src.knowledge.research_rag as rr

        persist_dir = tmp_path / "rag"
        persist_dir.mkdir()
        db_path = persist_dir / "research.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_pdf TEXT,
                    stock_code TEXT,
                    stock_name TEXT,
                    broker TEXT,
                    report_date TEXT,
                    page INTEGER,
                    text TEXT,
                    tokens TEXT
                )
                """
            )
            conn.execute(
                """INSERT INTO chunks
                   (source_pdf, stock_code, stock_name, broker, report_date, page, text, tokens)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "old.pdf", "600031", "X", "b", "2025-01-01", 1,
                    "核心观点\n公司海外订单增长，出口收入提升。",
                    "海外 订单 出口",
                ),
            )

        rag = ResearchRAG(persist_dir=persist_dir)
        stats = rag.get_stats()
        assert stats["total_chunks"] == 1
        assert stats["total_content_hashes"] == 1

        def fake_parse(_path):
            return [
                ResearchChunk(
                    stock_code="600031", stock_name="X", broker="b",
                    report_date="2025-01-01", page=1,
                    text="核心观点\n公司海外订单增长，出口收入提升。",
                )
            ]

        monkeypatch.setattr(rr, "parse_pdf_to_chunks", fake_parse)
        assert rag.ingest_pdf(tmp_path / "renamed.pdf") == 0
