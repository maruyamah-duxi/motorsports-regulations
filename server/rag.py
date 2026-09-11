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

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import quote

import httpx
import numpy as np

from . import crossref

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

EMBED_MODEL = os.environ.get("EMBED_MODEL", "gemini-embedding-001")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "768"))
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gemini-3.8-flash")

FTS_TOP = 30
VEC_TOP = 30
CONTEXT_CHUNKS = 8
RRF_K = 60

# 回答キャッシュ。いたずらは同じ質問の連打が大半なので、これが効く。
ANSWER_CACHE_TTL = float(os.environ.get("ANSWER_CACHE_TTL", "21600"))  # 6 時間
ANSWER_CACHE_SIZE = int(os.environ.get("ANSWER_CACHE_SIZE", "500"))

SYSTEM_INSTRUCTION = """\
あなたは JAF（日本自動車連盟）モータースポーツ諸規則の案内役です。
利用者は競技の参加者・オーガナイザー・オフィシャルです。

## 絶対に守ること

1. **与えられた「参考資料」に書かれていることだけ**を答えてください。
   資料に無いことは、一般的なモータースポーツの知識であっても答えないでください。
2. 記述の根拠になった資料には、必ず文末に [1] [2] のような番号を付けてください。
   番号は参考資料に振られたものをそのまま使います。複数あれば [1][3] のように並べます。
3. 条文の数値・基準・型式（例: FIA基準8858、45mm、70mm以下）は資料のとおりに、
   言い換えずに書いてください。

## 区分を取り違えないこと（最重要）

JAF の規則は車両区分ごとに別の条文を持ち、**似た条文が並んでいます**。
取り違えた数値を自信を持って示すことは、答えないことより悪い結果になります。

4. 利用者が車両区分（RRN／RJ／RPN／RF／AE など）を挙げているときは、
   引用しようとする条文が**その区分に適用されるか**を資料の文面で必ず確かめる。
5. 別の区分の規定だった場合、その数値を答えにしないでください。
   「〈数値〉は RPN・RF・AE 車両の規定で、RJ 車両には適用されません」のように
   区分を明示して述べます。
6. 資料に「〈他の規則〉に従う」という**参照だけ**があり、参照先の条文本体が
   資料に無い場合は、**数値を推測しないでください**。
   「RJ 車両は〈参照先の規則名・条〉に従います。その条文は参考資料に
   含まれていないため、原本 P.◯ をご確認ください」と答えて止まります。
7. 資料の中に答えが無い場合も同様に、推測せず「参考資料の中に該当する条文が
   見つかりませんでした」と述べ、関連しそうな規則名を挙げるに留めます。

## 回答の形

次の見出しで、日本語で書いてください。中身が無い項目は省いてかまいません。

**結論** — 1〜2 文。条件付きで可否が決まる場合は「〜であれば可」と条件込みで。

**条件** — 数値・材質・型式・取り付け方法を箇条書きで。資料のとおりに書く。

**適用範囲** — どの車両区分・どの規則のどの条の話かを 1 行で。
参照をたどって別の規則に行き着いた場合は、その経路も書く
（例:「第2編 5.2 が第1編 第4章 第6条に送っており、そちらが本体」）。

**注意** — 資料から読み取れない点、確認が必要な点があれば書く。無ければ省く。

長い前置きは書かないでください。最後に必ず次の 1 文を添えます:
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
    #: 参照をたどって足した場合、どの参照から来たか（例「第1編レース車両規定 第6条」）
    via: str | None = None

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
            # 参照をたどって足した根拠は、画面でもそう示す
            "via": self.via,
            # 根拠の前後を読みたいときは JAF の原本の該当ページへ送る
            "pdfPageUrl": (
                f"{self.pdf_url}#page={self.page}"
                if self.pdf_url and self.page
                else self.pdf_url
            ),
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
    followed: dict[int, str] = {}
    placeholders = ",".join("?" * len(ids))
    rows = con.execute(
        f"SELECT ch.id, ch.doc_id, ch.anchor, ch.heading, ch.heading_path,"
        f" ch.page_start, ch.text, d.title, d.pdf_url"
        f" FROM chunks ch JOIN docs d ON d.doc_id = ch.doc_id"
        f" WHERE ch.id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {r[0]: r for r in rows}

    # 二段目: 拾った条文の中の「◯◯に従う」という参照をたどる。
    # 規則が互いを指し合うので、これをやらないと支配している条文が根拠に
    # 入らない（詳細は server/crossref.py の docstring）。
    if match:
        extra = crossref.follow(con, [r[6] for r in rows], match, set(ids))
        if extra:
            holes = ",".join("?" * len(extra))
            more = con.execute(
                "SELECT ch.id, ch.doc_id, ch.anchor, ch.heading, ch.heading_path,"
                " ch.page_start, ch.text, d.title, d.pdf_url"
                " FROM chunks ch JOIN docs d ON d.doc_id = ch.doc_id"
                f" WHERE ch.id IN ({holes})",
                [cid for cid, _ in extra],
            ).fetchall()
            by_id.update({r[0]: r for r in more})
            followed = dict(extra)
            ids += [cid for cid, _ in extra]

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
                via=followed.get(cid),
            )
        )
    return sources


# ---------------------------------------------------------------------------
# 回答キャッシュ
# ---------------------------------------------------------------------------


@dataclass
class CachedAnswer:
    sources: list[dict[str, Any]]
    answer: str


class AnswerCache:
    """同じ質問への回答を使い回す（TTL 付き LRU）。

    いたずらや連打は同じ質問の繰り返しが大半なので、ここで止めると
    埋め込みも生成も呼ばずに済み、費用がゼロになる。

    * **単発の質問だけ**を対象にする。会話の続き（history あり）は文脈で
      答えが変わるのでキャッシュしない。
    * 根拠 0 件の結果もキャッシュする。「今日の天気は？」の連打が
      いちばん止めたいケースなので、ここを外すと意味が薄れる。
    * キーにデータ版（search.db の builtAt）を混ぜるので、規則が更新されれば
      自動的に無効になる。

    プロセス内に持つため、Cloud Run のインスタンスごとに別のキャッシュに
    なる。`--max-instances` を絞っていれば実用上は十分効く。
    """

    def __init__(self, ttl: float = ANSWER_CACHE_TTL, size: int = ANSWER_CACHE_SIZE) -> None:
        self.ttl = ttl
        self.size = size
        self._store: OrderedDict[str, tuple[float, CachedAnswer]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(question: str, data_version: str) -> str:
        normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", question)).strip().casefold()
        raw = f"{normalized}\x00{CHAT_MODEL}\x00{EMBED_MODEL}/{EMBED_DIM}\x00{data_version}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> CachedAnswer | None:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            stored_at, value = entry
            if now - stored_at > self.ttl:
                del self._store[key]
                self.misses += 1
                return None
            self._store.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: str, value: CachedAnswer) -> None:
        with self._lock:
            self._store[key] = (time.monotonic(), value)
            self._store.move_to_end(key)
            while len(self._store) > self.size:
                self._store.popitem(last=False)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._store),
                "hits": self.hits,
                "misses": self.misses,
                "ttlSeconds": int(self.ttl),
            }


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
