"""規則の条文を根拠にして答える RAG。

方針:
  * **根拠のある条文だけ**を答える。検索で引けなければ「該当なし」と返す。
  * 回答には必ず出典（規則名・条・ページ）を付け、本文へのアンカーを添える。
  * Gemini の API キーはここ（サーバ側）にだけ置く。クライアントには渡さない。

検索は全文検索とベクトル検索のハイブリッド。
  * FTS5(trigram) … 「第5条」「8858」のような語がそのまま出る質問に強い
  * ベクトル      … 「ヘルメットはどんなものを使えばいい？」の言い換えに強い
両者の順位を RRF（Reciprocal Rank Fusion）で融合する。

ベクトル検索は 8,778 チャンク × 768 次元＝約 27MB なので、拡張なしの
総当たり（numpy の行列積）で十分速い。sqlite-vec 等は導入しない。
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import quote

import httpx
import numpy as np

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

EMBED_MODEL = os.environ.get("EMBED_MODEL", "gemini-embedding-001")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "768"))
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gemini-3.8-flash")

FTS_TOP = 30
VEC_TOP = 30
CONTEXT_CHUNKS = 8
RRF_K = 60

SYSTEM_INSTRUCTION = """\
あなたは JAF（日本自動車連盟）モータースポーツ諸規則の案内役です。
利用者は競技の参加者・オーガナイザー・オフィシャルです。

## 絶対に守ること

1. **与えられた「参考資料」に書かれていることだけ**を答えてください。
   資料に無いことは、一般的なモータースポーツの知識であっても答えないでください。
2. 資料の中に答えが無い場合は、推測せず「参考資料の中に該当する条文が
   見つかりませんでした」と述べ、関連しそうな規則名があれば挙げるに留めてください。
3. 記述の根拠になった資料には、必ず文末に [1] [2] のような番号を付けてください。
   番号は参考資料に振られたものをそのまま使います。複数あれば [1][3] のように並べます。
4. 条文の数値・基準・型式（例: FIA基準8858、45mm、70mm以下）は資料のとおりに、
   言い換えずに書いてください。
5. 資料に複数の車両区分（RRN／RJ／RPN／AE／RF など）や競技種別ごとの規定がある場合は、
   どれについての規定かを明示してください。

## 書き方

- 日本語で、結論から簡潔に。
- 箇条書きを適度に使い、長い前置きは書かない。
- 最後に必ず次の 1 文を添える:
  「正式な判断は JAF の原本 PDF をご確認ください。」
"""


# 日本語の質問は空白で区切られていないため、そのまま FTS に渡すと
# 文全体が 1 つのフレーズになりヒットしない。文字種の切れ目で語を拾う。
#   「ロールケージの溶接は？」→ ロールケージ / 溶接
_RUN_RE = re.compile(
    r"[ァ-ヴー]+"          # カタカナ
    r"|[一-鿿々]+"          # 漢字
    r"|[A-Za-z][A-Za-z0-9.\-]*"  # 英数（FIA8858 のような型式）
    r"|[0-9][0-9.,]*[a-zA-Z]*"    # 数値（45mm など）
    r"|[ぁ-ゖ]+"            # ひらがな（助詞なので基本は捨てる）
)
# 単独では意味を持たない語。拾っても検索の役に立たない。
_STOPWORDS = {
    "場合", "以下", "以上", "とき", "こと", "もの", "ため", "regulations", "jaf",
}
MIN_TRIGRAM_LEN = 3


def extract_terms(text: str) -> list[str]:
    """質問文から検索語を切り出す（文字種の切れ目で分割）."""
    terms: list[str] = []
    for m in _RUN_RE.finditer(text):
        run = m.group(0)
        if re.fullmatch(r"[ぁ-ゖ]+", run):
            continue  # 助詞・活用語尾
        if run.lower() in _STOPWORDS or len(run) < 2:
            continue
        if run not in terms:
            terms.append(run)
    # 長い語ほど手がかりになる
    return sorted(terms, key=len, reverse=True)


def fts_query(terms: list[str], *, conjunction: str = "OR") -> str:
    """FTS5 のクエリ式にする。

    trigram トークナイザは 3 文字以上でないと引けないので、2 文字の語は落とす
    （そちらはベクトル検索と LIKE のフォールバックに任せる）。
    RAG の根拠集めでは取りこぼしを避けたいので既定は OR。
    """
    usable = [t.replace('"', "") for t in terms if len(t) >= MIN_TRIGRAM_LEN]
    if not usable:
        return ""
    return f" {conjunction} ".join(f'"{t}"' for t in usable)


@dataclass
class Source:
    index: int
    doc_id: str
    title: str
    heading: str
    heading_path: str
    page: int
    anchor: str | None
    text: str
    pdf_url: str | None

    @property
    def url(self) -> str:
        frag = f"#{quote(self.anchor)}" if self.anchor else f"#p{self.page}"
        return f"/doc/{quote(self.doc_id)}{frag}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "docId": self.doc_id,
            "title": self.title,
            "heading": self.heading,
            "headingPath": self.heading_path,
            "page": self.page,
            "anchor": self.anchor,
            "url": self.url,
            "pdfUrl": self.pdf_url,
            "excerpt": self.text[:200],
        }


class RagUnavailable(RuntimeError):
    """API キーが無い、ベクトルが無いなど、AI 回答を出せない状態."""


# ---------------------------------------------------------------------------
# ベクトルの読み込み（起動時に 1 回）
# ---------------------------------------------------------------------------


class VectorIndex:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._ids: np.ndarray | None = None
        self._matrix: np.ndarray | None = None
        self._loaded = False

    def load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            try:
                con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            except sqlite3.Error:
                return
            try:
                rows = con.execute(
                    "SELECT id, vec FROM chunks WHERE vec IS NOT NULL ORDER BY id"
                ).fetchall()
            except sqlite3.OperationalError:
                # 古い search.db には vec 列が無い。全文検索のみで動かす。
                return
            finally:
                con.close()
            if not rows:
                return
            ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
            mat = np.frombuffer(b"".join(r[1] for r in rows), dtype="<f4")
            dim = mat.size // len(rows)
            self._matrix = mat.reshape(len(rows), dim)
            self._ids = ids

    @property
    def ready(self) -> bool:
        self.load()
        return self._matrix is not None

    @property
    def count(self) -> int:
        self.load()
        return 0 if self._matrix is None else int(self._matrix.shape[0])

    def search(self, query_vec: np.ndarray, top: int) -> list[tuple[int, float]]:
        self.load()
        if self._matrix is None or self._ids is None:
            return []
        # ベクトルは取得時に L2 正規化済みなので、内積がそのままコサイン類似度
        scores = self._matrix @ query_vec.astype("<f4")
        top = min(top, scores.size)
        idx = np.argpartition(-scores, top - 1)[:top]
        idx = idx[np.argsort(-scores[idx])]
        return [(int(self._ids[i]), float(scores[i])) for i in idx]


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RagUnavailable("GEMINI_API_KEY が設定されていません")
    return key


def embed_query(text: str, client: httpx.Client) -> np.ndarray:
    r = client.post(
        f"{API_BASE}/models/{EMBED_MODEL}:embedContent",
        params={"key": _api_key()},
        json={
            "model": f"models/{EMBED_MODEL}",
            "content": {"parts": [{"text": text[:1600]}]},
            "taskType": "RETRIEVAL_QUERY",
            "outputDimensionality": EMBED_DIM,
        },
    )
    r.raise_for_status()
    values = r.json()["embedding"]["values"]
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return np.array([v / norm for v in values], dtype="<f4")


def stream_answer(
    question: str,
    sources: list[Source],
    history: list[dict[str, str]],
    client: httpx.Client,
) -> Iterator[str]:
    """Gemini の応答を逐次返す."""
    reference = "\n\n".join(
        f"[{s.index}] {s.title}\n"
        f"    位置: {s.heading_path or s.heading} ／ P.{s.page}\n"
        f"    本文: {s.text}"
        for s in sources
    )
    contents: list[dict[str, Any]] = []
    for turn in history[-6:]:
        role = "user" if turn.get("role") == "user" else "model"
        text = (turn.get("text") or "").strip()
        if text:
            contents.append({"role": role, "parts": [{"text": text[:4000]}]})
    contents.append(
        {
            "role": "user",
            "parts": [{"text": f"# 参考資料\n\n{reference}\n\n# 質問\n\n{question}"}],
        }
    )

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2048},
    }

    with client.stream(
        "POST",
        f"{API_BASE}/models/{CHAT_MODEL}:streamGenerateContent",
        params={"key": _api_key(), "alt": "sse"},
        json=payload,
    ) as r:
        if r.status_code >= 400:
            body = b"".join(r.iter_bytes()).decode("utf-8", "replace")
            raise RuntimeError(f"Gemini {r.status_code}: {body[:300]}")
        for line in r.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                chunk = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for cand in chunk.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    text = part.get("text")
                    if text:
                        yield text


# ---------------------------------------------------------------------------
# 検索（ハイブリッド）
# ---------------------------------------------------------------------------


def retrieve(
    con: sqlite3.Connection,
    question: str,
    vectors: VectorIndex,
    client: httpx.Client,
    normalized_question: str,
) -> list[Source]:
    """全文検索とベクトル検索の順位を RRF で融合して根拠チャンクを選ぶ."""
    ranks: dict[int, float] = {}
    terms = extract_terms(normalized_question)

    match = fts_query(terms, conjunction="OR")
    if match:
        rows = con.execute(
            "SELECT ch.id FROM chunks_fts JOIN chunks ch ON ch.id = chunks_fts.rowid"
            " WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
            (match, FTS_TOP),
        ).fetchall()
        for rank, (chunk_id,) in enumerate(rows):
            ranks[chunk_id] = ranks.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    else:
        # 3 文字以上の語が無いとき（「溶接」だけ等）は LIKE で拾う
        for term in terms[:2]:
            rows = con.execute(
                "SELECT id FROM chunks WHERE text_norm LIKE ? LIMIT ?",
                (f"%{term}%", FTS_TOP),
            ).fetchall()
            for rank, (chunk_id,) in enumerate(rows):
                ranks[chunk_id] = ranks.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)

    if vectors.ready:
        qvec = embed_query(question, client)
        for rank, (chunk_id, _score) in enumerate(vectors.search(qvec, VEC_TOP)):
            ranks[chunk_id] = ranks.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)

    if not ranks:
        return []

    best = sorted(ranks.items(), key=lambda kv: -kv[1])[:CONTEXT_CHUNKS]
    ids = [cid for cid, _ in best]
    placeholders = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT ch.id, ch.doc_id, ch.anchor, ch.heading, ch.heading_path,"
        f" ch.page_start, ch.text, d.title, d.pdf_url"
        f" FROM chunks ch JOIN docs d ON d.doc_id = ch.doc_id"
        f" WHERE ch.id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {r[0]: r for r in rows}

    sources: list[Source] = []
    for i, cid in enumerate(ids, 1):
        r = by_id.get(cid)
        if not r:
            continue
        sources.append(
            Source(
                index=i,
                doc_id=r[1],
                title=r[7],
                heading=r[3] or "",
                heading_path=r[4] or "",
                page=r[5] or 1,
                anchor=r[2],
                text=r[6],
                pdf_url=r[8],
            )
        )
    return sources


# ---------------------------------------------------------------------------
# 簡易レート制限
# ---------------------------------------------------------------------------


class RateLimiter:
    """IP ごとのトークンバケット。

    公開エンドポイントから有料 API を呼ぶので、最低限の歯止めを入れる。
    Cloud Run はインスタンスが増減するためこれは厳密な制限にはならない。
    本格的に絞るなら Cloud Armor などを前段に置くこと。
    """

    def __init__(self, capacity: int = 10, refill_seconds: float = 30.0) -> None:
        self.capacity = capacity
        self.refill = refill_seconds
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._state.get(key, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - last) * self.capacity / self.refill)
            if tokens < 1.0:
                self._state[key] = (tokens, now)
                return False
            self._state[key] = (tokens - 1.0, now)
            if len(self._state) > 10000:  # 際限なく増やさない
                self._state = {key: (tokens - 1.0, now)}
            return True
