"""Tests for evidence_claims store + import script (P2-3)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.import_evidence_claims import load_csv, VALID_EVIDENCE_TYPES  # noqa: E402
from src.storage import DuckDBStore  # noqa: E402


@pytest.fixture
def store(tmp_path):
    s = DuckDBStore(db_path=tmp_path / "t.duckdb")
    yield s
    s.close()


class TestEvidenceClaimsStore:
    def test_save_and_load_roundtrip(self, store):
        df = pd.DataFrame([
            {
                "code": "600031", "name": "三一重工",
                "claim_text": "Q1 营收同比 +14%",
                "claim_source": "broker_report_title",
                "broker": "东吴证券",
                "report_date": pd.Timestamp("2026-04-30").date(),
                "report_id": "AP1",
                "evidence_type": "capacity",
                "confidence": "high",
                "tags_json": '["upcycle"]',
                "raw_text": "...",
                "source_url": "",
            },
        ])
        n = store.save_evidence_claims(df)
        assert n == 1

        loaded = store.load_evidence_claims(code="600031")
        assert len(loaded) == 1
        assert loaded.iloc[0]["evidence_type"] == "capacity"

    def test_load_filter_by_evidence_type(self, store):
        df = pd.DataFrame([
            {"code": "600276", "claim_text": "BD 收入", "evidence_type": "license_out",
             "report_date": pd.Timestamp("2026-05-07").date(), "report_id": "r1"},
            {"code": "600276", "claim_text": "产能", "evidence_type": "capacity",
             "report_date": pd.Timestamp("2026-05-08").date(), "report_id": "r2"},
        ])
        store.save_evidence_claims(df)

        loaded = store.load_evidence_claims(code="600276", evidence_type="license_out")
        assert len(loaded) == 1
        assert loaded.iloc[0]["evidence_type"] == "license_out"

    def test_upsert_replaces_existing(self, store):
        """同 (code, report_date, claim_text, evidence_type, report_id) 主键覆盖。"""
        df1 = pd.DataFrame([{
            "code": "600276", "claim_text": "BD 收入", "evidence_type": "license_out",
            "report_date": pd.Timestamp("2026-05-07").date(), "report_id": "r1",
            "confidence": "medium",
        }])
        df2 = pd.DataFrame([{
            "code": "600276", "claim_text": "BD 收入", "evidence_type": "license_out",
            "report_date": pd.Timestamp("2026-05-07").date(), "report_id": "r1",
            "confidence": "high",  # upgrade
        }])
        store.save_evidence_claims(df1)
        store.save_evidence_claims(df2)

        loaded = store.load_evidence_claims(code="600276")
        assert len(loaded) == 1
        assert loaded.iloc[0]["confidence"] == "high"


class TestImportScriptValidation:
    def test_valid_evidence_types_complete(self):
        assert VALID_EVIDENCE_TYPES == {
            "overseas_order", "capacity", "customer",
            "license_out", "fda_cde", "vbp_event", "guidance",
        }

    def test_load_csv_normalizes_code_padding(self, tmp_path):
        csv_path = tmp_path / "seed.csv"
        csv_path.write_text(
            "code,name,claim_text,claim_source,broker,report_date,report_id,"
            "evidence_type,confidence,tags_json,raw_text,source_url\n"
            "31,X,test claim,src,b,2026-05-07,r1,capacity,high,[],x,\n",
            encoding="utf-8",
        )
        df = load_csv(csv_path)
        assert df.iloc[0]["code"] == "000031"

    def test_load_csv_rejects_invalid_evidence_type(self, tmp_path):
        csv_path = tmp_path / "seed.csv"
        csv_path.write_text(
            "code,name,claim_text,claim_source,broker,report_date,report_id,"
            "evidence_type,confidence,tags_json,raw_text,source_url\n"
            "600031,X,test,src,b,2026-05-07,r1,bogus_type,high,[],x,\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="validation errors"):
            load_csv(csv_path)

    def test_load_csv_rejects_missing_report_date(self, tmp_path):
        """report_date 必填，避免同 code+claim NULL date 在 DuckDB 堆积。"""
        csv_path = tmp_path / "seed.csv"
        csv_path.write_text(
            "code,name,claim_text,claim_source,broker,report_date,report_id,"
            "evidence_type,confidence,tags_json,raw_text,source_url\n"
            "600031,X,test,src,b,,r1,capacity,high,[],x,\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="validation errors"):
            load_csv(csv_path)

    def test_load_csv_rejects_invalid_confidence(self, tmp_path):
        csv_path = tmp_path / "seed.csv"
        csv_path.write_text(
            "code,name,claim_text,claim_source,broker,report_date,report_id,"
            "evidence_type,confidence,tags_json,raw_text,source_url\n"
            "600031,X,test,src,b,2026-05-07,r1,capacity,bogus_conf,[],x,\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="validation errors"):
            load_csv(csv_path)

    def test_seed_csv_loads_clean(self):
        """内置 seed CSV 必须可被加载（防止 schema 漂移）。"""
        seed = PROJECT_ROOT / "tests/fixtures/evidence_claims_seed.csv"
        df = load_csv(seed)
        assert len(df) >= 4
        assert set(df["evidence_type"]).issubset(VALID_EVIDENCE_TYPES)
