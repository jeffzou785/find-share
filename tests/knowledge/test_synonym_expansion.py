"""RAG 同义词扩展测试（P1.5-5）。

验证：
- expand_query_synonyms 正确扩展
- 同义词查询的检索召回率提升
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.knowledge.research_rag import (
    ResearchRAG,
    expand_query_synonyms,
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
        from src.knowledge.research_rag import ResearchChunk
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
