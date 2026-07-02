"""研报 RAG 知识层（轻量版：jieba + TF-IDF + SQLite + 同义词扩展，不依赖 chromadb）。

工作原理：
1. PDF 解析 → 按页分块
2. jieba 分词，去掉停用词
3. 用 TF-IDF 算每个 chunk 的词向量
4. SQLite 存 chunks 文本 + metadata，pickle 存 TF-IDF 矩阵
5. 查询时：
   - P1.5-5：同义词扩展（如"海外"扩展为"海外/境外/国外/出口"）
   - metadata 强过滤（stock_code / broker）
   - 算 query 的 TF-IDF → 余弦相似度排序

权衡：
- 优点：零额外模型下载，秒级启动，完全本地
- 局限：TF-IDF + 同义词比 embedding 简单，但已能解决"海外 vs 境外"这类常见召回问题
- 升级路径：研报量 >100 后再评估 sentence-transformers（参见 IMPROVEMENTS §5.5）
"""
from __future__ import annotations

import hashlib
import math
import pickle
import re
import sqlite3
import warnings
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pdfplumber

warnings.filterwarnings("ignore", category=UserWarning, module="pdfplumber")

CHUNK_SIZE_CHARS = 1200
CHUNK_OVERLAP_CHARS = 200

# 简单中文停用词表
_STOPWORDS = set(
    """的 了 和 是 在 与 或 也 都 就 还 又 这 那 一个 一种 一些 一项
    我们 你们 他们 它们 该 此 其 之 以 等 为 对 从 向 给 跟 至
    及 等 等 中 内 上 下 而 但 因 所以 如果 因为 还 比如 例如
    不 没 没有 非 未 无 不是 不会 不能 不要 不可
    有 是 有没有 是否 什么 怎么 怎样 如何
    将 被让 使 之 至 此 该 这个 那个 这些 那些
    万元 亿元 元 千元 百分 之 个
    图 表 如下图 如上图 见图 见表
    报告 年报 一季报 三季报 中报 半年报 公司 企业 业务 行业 中国
    分点 同比 环比 增长 下降 增加 减少 维持 持平
    收入 利润 营收 净利 营业 资产 负债 现金
    分析师 研究员 研报 券商
  """.split()
)

# P1.5-5：同义词词典（双向扩展）。
# 查询含任一词时，扩展到该组所有词。
SYNONYM_GROUPS: list[set[str]] = [
    # 海外/出口主题
    {"海外", "境外", "国外", "出口", "国际", "外销"},
    # 订单/合同
    {"订单", "中标", "签约", "合同", "项目"},
    # 一带一路
    {"一带一路", "丝路", "BR"},
    # 区域：欧洲
    {"欧洲", "欧盟", "EU", "欧洲区"},
    # 区域：美洲
    {"美洲", "北美", "南美", "美加"},
    # 区域：亚洲（除国内）
    {"东南亚", "东盟", "ASEAN"},
    {"日韩", "日本", "韩国"},
    # 渠道/销售
    {"渠道", "经销", "代理"},
    # 产能/扩产
    {"产能", "扩产", "投产", "新建"},
    # 业绩/超预期
    {"业绩", "增长", "超预期", "亮眼"},
]

# 反向索引：词 → 同义词组（用于查询扩展）
_SYNONYM_INDEX: dict[str, set[str]] = {}
for _grp in SYNONYM_GROUPS:
    for _w in _grp:
        _SYNONYM_INDEX.setdefault(_w, set()).update(_grp)


def expand_query_synonyms(tokens: list[str]) -> list[str]:
    """P1.5-5：用同义词词典扩展 query tokens。

    输入 ["海外", "订单"] → 输出 ["海外", "境外", "国外", "出口", "国际", "外销",
                              "订单", "中标", "签约", "合同", "项目"]
    保留原始 token 顺序，扩展的同义词追加在后（降低其在 tf 中的权重）。
    """
    expanded = list(tokens)
    seen = set(tokens)
    for t in tokens:
        syns = _SYNONYM_INDEX.get(t)
        if not syns:
            continue
        for s in syns:
            if s not in seen:
                expanded.append(s)
                seen.add(s)
    return expanded


@dataclass
class ResearchChunk:
    stock_code: str
    stock_name: str
    broker: str
    report_date: str
    page: int
    text: str
    section_title: str = ""


@dataclass
class SearchResult:
    score: float
    text: str
    stock_code: str
    stock_name: str
    broker: str
    report_date: str
    page: int
    source_pdf: str
    section_title: str = ""


_HEADING_PATTERNS = [
    re.compile(r"^\s*(第[一二三四五六七八九十]+[章节])\s*[:：、]?\s*(.{2,40})\s*$"),
    re.compile(r"^\s*([一二三四五六七八九十]+[、.．])\s*(.{2,40})\s*$"),
    re.compile(r"^\s*（([一二三四五六七八九十]+)）\s*(.{2,40})\s*$"),
    re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){1,2})\s*(.{2,40})\s*$"),
    re.compile(r"^\s*(\d{1,2})[、.．]\s*(.{2,40})\s*$"),
    re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,2})\s+(.{2,40})\s*$"),
]


def _normalize_for_hash(text: str) -> str:
    """内容去重用的规范化：去空白、统一大小写。"""
    return re.sub(r"\s+", "", text or "").lower()


def _hash_text(text: str) -> str:
    return hashlib.sha256(_normalize_for_hash(text).encode("utf-8")).hexdigest()


def _content_hash(chunks: list[ResearchChunk]) -> str:
    joined = "\n".join(c.text for c in chunks)
    return _hash_text(joined)


def _clean_heading(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(r"[。；;：:,.，]+$", "", title).strip()
    return title[:80]


def infer_section_title(text: str) -> str:
    """从文本片段中推断章节标题。

    只做保守规则，避免把普通正文误判成章节。
    """
    for raw_line in (text or "").splitlines()[:12]:
        line = raw_line.strip()
        if not (2 <= len(line) <= 60):
            continue
        for pat in _HEADING_PATTERNS:
            m = pat.match(line)
            if m:
                return _clean_heading(m.group(2) if len(m.groups()) >= 2 else line)
        if line in {"投资建议", "盈利预测", "风险提示", "公司概况", "核心观点"}:
            return line
    return ""


def _extract_section_headings(text: str) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    cursor = 0
    for raw_line in text.splitlines():
        line_start = cursor
        line = raw_line.strip()
        title = infer_section_title(line)
        if title:
            headings.append((line_start, title))
        cursor += len(raw_line) + 1
    return headings


def _section_for_span(
    page_text: str,
    start: int,
    end: int,
    headings: list[tuple[int, str]],
) -> str:
    for pos, title in headings:
        if start <= pos <= min(end, start + 300):
            return title
    before = [title for pos, title in headings if pos <= start]
    if before:
        return before[-1]
    return infer_section_title(page_text[start:end])


def _tokenize(text: str) -> list[str]:
    """jieba 分词 + 过滤停用词和单字符。"""
    import jieba
    tokens = jieba.lcut(text)
    return [
        t.strip()
        for t in tokens
        if len(t.strip()) >= 2 and t.strip() not in _STOPWORDS and not t.strip().isdigit()
    ]


def parse_pdf_to_chunks(pdf_path: str | Path) -> list[ResearchChunk]:
    """解析研报 PDF 成 chunks。

    文件名格式：{stock_code}_{stock_name}_{broker}_{date}.pdf
    """
    pdf_path = Path(pdf_path)
    parts = pdf_path.stem.split("_")
    if len(parts) >= 4:
        stock_code, stock_name, broker, date = parts[0], parts[1], parts[2], parts[3]
    else:
        stock_code = parts[0] if parts else "unknown"
        stock_name = parts[1] if len(parts) > 1 else "unknown"
        broker = parts[2] if len(parts) > 2 else "unknown"
        date = ""

    chunks: list[ResearchChunk] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = (page.extract_text() or "").strip()
                if len(text) < 100:
                    continue
                headings = _extract_section_headings(text)
                for chunk_text, start, end in _split_text_with_offsets(
                    text, CHUNK_SIZE_CHARS, CHUNK_OVERLAP_CHARS
                ):
                    if len(chunk_text.strip()) < 50:
                        continue
                    chunks.append(
                        ResearchChunk(
                            stock_code=stock_code,
                            stock_name=stock_name,
                            broker=broker,
                            report_date=date,
                            page=page_num,
                            text=chunk_text,
                            section_title=_section_for_span(text, start, end, headings),
                        )
                    )
    except Exception as e:
        print(f"  ✗ 解析 {pdf_path.name} 失败: {type(e).__name__}: {e}")
    return chunks


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    return [chunk for chunk, _, _ in _split_text_with_offsets(text, size, overlap)]


def _split_text_with_offsets(text: str, size: int, overlap: int) -> list[tuple[str, int, int]]:
    if len(text) <= size:
        return [(text, 0, len(text))]
    parts = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        parts.append((text[start:end], start, end))
        start += size - overlap
    return parts


class ResearchRAG:
    """研报 RAG（TF-IDF + SQLite 实现）。"""

    def __init__(self, persist_dir: Path | None = None):
        from ..config import config

        self.persist_dir = persist_dir or (config.CACHE_DIR / "rag")
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.persist_dir / "research.db"
        self.index_path = self.persist_dir / "tfidf.pkl"

        self._init_db()
        self._tfidf_matrix: Optional[np.ndarray] = None
        self._vocab: Optional[dict[str, int]] = None
        self._idf: Optional[np.ndarray] = None
        self._row_norms: Optional[np.ndarray] = None
        self._chunk_count: int = 0
        self._load_index()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_pdf TEXT,
                    stock_code TEXT,
                    stock_name TEXT,
                    broker TEXT,
                    report_date TEXT,
                    page INTEGER,
                    section_title TEXT,
                    content_hash TEXT,
                    text_hash TEXT,
                    text TEXT,
                    tokens TEXT
                )
                """
            )
            self._add_column_if_missing(conn, "section_title", "TEXT")
            self._add_column_if_missing(conn, "content_hash", "TEXT")
            self._add_column_if_missing(conn, "text_hash", "TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_stock ON chunks(stock_code)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_source ON chunks(source_pdf)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_content_hash ON chunks(content_hash)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_text_hash ON chunks(text_hash)"
            )
            self._backfill_hash_columns(conn)

    @staticmethod
    def _add_column_if_missing(conn, column: str, ddl_type: str) -> None:
        rows = conn.execute("PRAGMA table_info(chunks)").fetchall()
        existing = {r[1] for r in rows}
        if column not in existing:
            conn.execute(f"ALTER TABLE chunks ADD COLUMN {column} {ddl_type}")

    @staticmethod
    def _backfill_hash_columns(conn) -> None:
        rows = conn.execute(
            "SELECT id, source_pdf, text, content_hash, text_hash FROM chunks "
            "WHERE text IS NOT NULL"
        ).fetchall()
        if not rows:
            return

        by_source: dict[str, list[tuple[int, str]]] = {}
        for chunk_id, source_pdf, text, content_hash, text_hash in rows:
            if not text_hash:
                conn.execute(
                    "UPDATE chunks SET text_hash = ? WHERE id = ?",
                    [_hash_text(text), chunk_id],
                )
            if source_pdf:
                by_source.setdefault(source_pdf, []).append((chunk_id, text))

        for source_pdf, source_rows in by_source.items():
            existing = conn.execute(
                "SELECT content_hash FROM chunks "
                "WHERE source_pdf = ? AND content_hash IS NOT NULL "
                "AND content_hash != '' LIMIT 1",
                [source_pdf],
            ).fetchone()
            content_hash = (
                existing[0] if existing
                else _hash_text("\n".join(text for _, text in source_rows))
            )
            conn.execute(
                "UPDATE chunks SET content_hash = ? "
                "WHERE source_pdf = ? AND (content_hash IS NULL OR content_hash = '')",
                [content_hash, source_pdf],
            )

    @property
    def count(self) -> int:
        if self._chunk_count > 0:
            return self._chunk_count
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
            self._chunk_count = row[0] if row else 0
            return self._chunk_count

    def _get_indexed_pdfs(self) -> set[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT DISTINCT source_pdf FROM chunks").fetchall()
        return {r[0] for r in rows if r[0]}

    def _get_indexed_content_hashes(self) -> set[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT content_hash FROM chunks "
                "WHERE content_hash IS NOT NULL AND content_hash != ''"
            ).fetchall()
        return {r[0] for r in rows if r[0]}

    def ingest_pdf(
        self,
        pdf_path: Path | str,
        metadata: dict | None = None,
    ) -> int:
        """索引单份研报 PDF。返回新增 chunk 数。

        metadata 可显式传入 stock_code / stock_name / broker / report_date，
        不传则从文件名解析（parse_pdf_to_chunks 默认逻辑）。
        若该 PDF 已索引（按文件名或内容 hash 去重），返回 0。
        """
        pdf_path = Path(pdf_path)
        source_key = pdf_path.name
        if source_key in self._get_indexed_pdfs():
            return 0

        chunks = parse_pdf_to_chunks(pdf_path)
        if not chunks:
            return 0
        content_hash = _content_hash(chunks)
        if content_hash in self._get_indexed_content_hashes():
            return 0

        # metadata 覆盖文件名解析
        if metadata:
            for c in chunks:
                if metadata.get("stock_code"):
                    c.stock_code = str(metadata["stock_code"])
                if metadata.get("stock_name"):
                    c.stock_name = str(metadata["stock_name"])
                if metadata.get("broker"):
                    c.broker = str(metadata["broker"])
                if metadata.get("report_date"):
                    c.report_date = str(metadata["report_date"])

        added = self._add_chunks(source_key, chunks, content_hash=content_hash)
        if added > 0:
            self._build_index()
        return added

    def index_directory(self, pdf_dir: Path | str, force_refresh: bool = False) -> int:
        """索引整个研报目录。返回新增 chunk 数。"""
        pdf_dir = Path(pdf_dir)
        pdfs = sorted(pdf_dir.glob("**/*.pdf"))

        if force_refresh:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM chunks")
            self._chunk_count = 0
            for f in [self.index_path]:
                if f.exists():
                    f.unlink()

        existing = self._get_indexed_pdfs() if not force_refresh else set()
        content_hashes = self._get_indexed_content_hashes() if not force_refresh else set()
        total = 0

        from tqdm import tqdm

        for pdf_path in tqdm(pdfs, desc="索引研报", ncols=80):
            if pdf_path.name in existing:
                continue
            chunks = parse_pdf_to_chunks(pdf_path)
            if not chunks:
                continue
            content_hash = _content_hash(chunks)
            if content_hash in content_hashes:
                continue
            added = self._add_chunks(pdf_path.name, chunks, content_hash=content_hash)
            if added:
                content_hashes.add(content_hash)
                total += added

        if total > 0:
            self._build_index()

        return total

    def _add_chunks(
        self,
        source_pdf: str,
        chunks: list[ResearchChunk],
        content_hash: str | None = None,
    ) -> int:
        if not chunks:
            return 0
        content_hash = content_hash or _content_hash(chunks)
        added = 0
        with sqlite3.connect(self.db_path) as conn:
            seen_text_hashes: set[str] = set()
            for c in chunks:
                text_hash = _hash_text(c.text)
                if text_hash in seen_text_hashes:
                    continue
                tokens = " ".join(_tokenize(c.text))
                conn.execute(
                    """INSERT INTO chunks
                       (source_pdf, stock_code, stock_name, broker, report_date,
                        page, section_title, content_hash, text_hash, text, tokens)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source_pdf, c.stock_code, c.stock_name, c.broker,
                        c.report_date, c.page, c.section_title,
                        content_hash, text_hash, c.text, tokens,
                    ),
                )
                seen_text_hashes.add(text_hash)
                added += 1
        self._chunk_count += added
        return added

    def _load_all_chunks(self) -> list[tuple[int, dict]]:
        """加载所有 chunks。返回 [(id, {text, tokens, metadata})]。"""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT id, source_pdf, stock_code, stock_name, broker,
                          report_date, page, section_title, content_hash,
                          text_hash, text, tokens FROM chunks"""
            ).fetchall()
        return [
            (
                r[0],
                {
                    "source_pdf": r[1],
                    "stock_code": r[2],
                    "stock_name": r[3],
                    "broker": r[4],
                    "report_date": r[5],
                    "page": r[6],
                    "section_title": r[7] or "",
                    "content_hash": r[8],
                    "text_hash": r[9],
                    "text": r[10],
                    "tokens": r[11].split() if r[11] else [],
                },
            )
            for r in rows
        ]

    def _build_index(self) -> None:
        """构建 TF-IDF 矩阵。"""
        chunks = self._load_all_chunks()
        if not chunks:
            return

        # 构建词表
        df_counter: Counter = Counter()
        doc_tokens: list[list[str]] = []
        for _, c in chunks:
            tokens = c["tokens"]
            doc_tokens.append(tokens)
            for t in set(tokens):
                df_counter[t] += 1

        vocab = {t: i for i, t in enumerate(sorted(df_counter.keys()))}
        n_docs = len(chunks)
        idf = np.array(
            [math.log((1 + n_docs) / (1 + df_counter[t])) + 1 for t in vocab]
        )

        # 构建 TF-IDF 矩阵
        matrix = np.zeros((n_docs, len(vocab)), dtype=np.float32)
        for i, tokens in enumerate(doc_tokens):
            tf = Counter(tokens)
            for t, c in tf.items():
                j = vocab.get(t)
                if j is not None:
                    matrix[i, j] = c
        matrix *= idf  # 广播

        # 行 L2 归一化
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix = matrix / norms

        # 保存
        with self.index_path.open("wb") as f:
            pickle.dump({"vocab": vocab, "idf": idf, "matrix": matrix, "n_docs": n_docs}, f)

        self._vocab = vocab
        self._idf = idf
        self._tfidf_matrix = matrix
        self._row_norms = norms.flatten()
        self._chunk_count = n_docs

    def _load_index(self) -> None:
        if not self.index_path.exists():
            return
        try:
            with self.index_path.open("rb") as f:
                d = pickle.load(f)
            self._vocab = d["vocab"]
            self._idf = d["idf"]
            self._tfidf_matrix = d["matrix"]
            self._chunk_count = d["n_docs"]
        except Exception:
            pass

    def _compute_query_vector(self, query: str) -> np.ndarray:
        tokens = _tokenize(query)
        # P1.5-5：同义词扩展（"海外" → "海外/境外/国外/出口/国际/外销"）
        tokens = expand_query_synonyms(tokens)
        if not tokens or self._vocab is None:
            return np.zeros(0, dtype=np.float32)
        tf = Counter(tokens)
        vec = np.zeros(len(self._vocab), dtype=np.float32)
        for t, c in tf.items():
            j = self._vocab.get(t)
            if j is not None:
                vec[j] = c
        vec *= self._idf
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    def query(
        self,
        query_text: str,
        stock_code: Optional[str] = None,
        broker: Optional[str] = None,
        top_k: int = 8,
    ) -> list[SearchResult]:
        """语义检索（TF-IDF 余弦相似度）。"""
        if self._chunk_count == 0 or self._tfidf_matrix is None:
            return []

        qvec = self._compute_query_vector(query_text)
        if qvec.size == 0 or np.linalg.norm(qvec) == 0:
            return []

        sims = self._tfidf_matrix @ qvec  # 余弦相似度（已归一化）

        # 过滤：股票代码/券商
        chunks = self._load_all_chunks()
        mask = np.array([True] * len(chunks))
        if stock_code:
            mask &= np.array([c["stock_code"] == stock_code for _, c in chunks])
        if broker:
            mask &= np.array([c["broker"] == broker for _, c in chunks])

        sims_masked = np.where(mask, sims, -1.0)
        # 取 top_k
        if (sims_masked >= 0).sum() == 0:
            return []

        top_k = min(top_k, int((sims_masked >= 0).sum()))
        top_indices = np.argsort(-sims_masked)[:top_k]

        out = []
        for idx in top_indices:
            score = float(sims_masked[idx])
            if score <= 0:
                continue
            _, c = chunks[idx]
            out.append(
                SearchResult(
                    score=score,
                    text=c["text"],
                    stock_code=c["stock_code"],
                    stock_name=c["stock_name"],
                    broker=c["broker"],
                    report_date=c["report_date"],
                    page=c["page"],
                    source_pdf=c["source_pdf"],
                    section_title=c.get("section_title") or "",
                )
            )
        return out

    def get_stats(self) -> dict:
        """返回索引统计信息。"""
        with sqlite3.connect(self.db_path) as conn:
            stock_rows = conn.execute(
                "SELECT stock_code, COUNT(*) FROM chunks GROUP BY stock_code ORDER BY COUNT(*) DESC LIMIT 20"
            ).fetchall()
            broker_rows = conn.execute(
                "SELECT broker, COUNT(*) FROM chunks GROUP BY broker ORDER BY COUNT(*) DESC"
            ).fetchall()
            pdf_rows = conn.execute(
                "SELECT COUNT(DISTINCT source_pdf) FROM chunks"
            ).fetchone()
            content_rows = conn.execute(
                "SELECT COUNT(DISTINCT content_hash) FROM chunks "
                "WHERE content_hash IS NOT NULL AND content_hash != ''"
            ).fetchone()
        return {
            "total_chunks": self.count,
            "total_pdfs": pdf_rows[0] if pdf_rows else 0,
            "total_content_hashes": content_rows[0] if content_rows else 0,
            "by_stock": [(r[0], r[1]) for r in stock_rows],
            "by_broker": [(r[0], r[1]) for r in broker_rows],
        }
