#!/usr/bin/env python3
"""検索チャンクの埋め込みベクトルを取得してキャッシュする.

全文検索（FTS5 trigram）は語がそのまま出てくる質問に強い一方、
「ヘルメットはどんなものを使えばいい？」のような言い換えには弱い。
そこを埋めるためにベクトル検索を併用する。

    export GEMINI_API_KEY=...
    python pipeline/build_embeddings.py            # 未取得のチャンクだけ
    python pipeline/build_embeddings.py --limit 20 # 動作確認用
    python pipeline/build_embeddings.py --estimate # 費用の見積りだけ出す

ベクトルは **本文のハッシュをキーに** `data/embeddings.sqlite` へ貯めます。
`search.db` は毎回作り直しますが、本文が変わらないチャンクは再取得しません。
規則が改訂されて本文が変わったチャンクだけが次回の対象になります。

重要: gemini-embedding-001 は出力次元を 3072 未満にすると
**正規化されていないベクトル**を返すため、こちらで L2 正規化します
（これを忘れると内積がコサイン類似度になりません）。
"""

from __future__ import annotations

import argparse
import array
import math
import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "search.db"
DEFAULT_CACHE = ROOT / "data" / "embeddings.sqlite"

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-embedding-001"
DEFAULT_DIM = 768
# gemini-embedding-001 の入力上限は 2,048 トークン。日本語はおおむね
# 1 文字 1 トークン前後なので、余裕を見て文字数で切る。
MAX_CHARS = 1600
BATCH = 32

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
  text_hash  TEXT NOT NULL,
  model      TEXT NOT NULL,
  dim        INTEGER NOT NULL,
  vec        BLOB NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (text_hash, model, dim)
);
"""


def l2_normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0:
        return values
    return [v / norm for v in values]


def pack(values: list[float]) -> bytes:
    """float32 のリトルエンディアン配列にする（numpy と直接やり取りできる形）."""
    arr = array.array("f", values)
    if sys.byteorder != "little":
        arr.byteswap()
    return arr.tobytes()


class EmbeddingClient:
    def __init__(self, api_key: str, model: str, dim: int, timeout: float = 120.0) -> None:
        self.model = model
        self.dim = dim
        self._client = httpx.Client(timeout=timeout)
        self._key = api_key

    def embed_batch(self, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
        url = f"{API_BASE}/models/{self.model}:batchEmbedContents"
        payload = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": t[:MAX_CHARS]}]},
                    "taskType": task_type,
                    "outputDimensionality": self.dim,
                }
                for t in texts
            ]
        }
        data = self._post(url, payload)
        embeddings = data.get("embeddings") or []
        if len(embeddings) != len(texts):
            raise RuntimeError(f"要求 {len(texts)} 件に対し {len(embeddings)} 件の応答")
        return [l2_normalize(e["values"]) for e in embeddings]

    def _post(self, url: str, payload: dict) -> dict:
        delay = 2.0
        last: Exception | None = None
        for attempt in range(6):
            try:
                r = self._client.post(url, params={"key": self._key}, json=payload)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"{r.status_code} {r.text[:200]}", request=r.request, response=r
                    )
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last = exc
                if attempt == 5:
                    break
                print(f"    再試行 {attempt + 1}/5（{delay:.0f}秒待機）: {str(exc)[:120]}", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 60)
        raise RuntimeError(f"埋め込みの取得に失敗しました: {last}")

    def close(self) -> None:
        self._client.close()


def open_cache(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(CACHE_SCHEMA)
    return con


def pending_chunks(db: Path, cache: sqlite3.Connection, model: str, dim: int) -> list[tuple[str, str]]:
    """まだベクトルを持っていない (text_hash, text) を返す（重複は除く）."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT text_hash, MIN(text) FROM chunks GROUP BY text_hash"
        ).fetchall()
    finally:
        con.close()

    have = {
        h
        for (h,) in cache.execute(
            "SELECT text_hash FROM vectors WHERE model = ? AND dim = ?", (model, dim)
        )
    }
    return [(h, t) for h, t in rows if h not in have]


def main() -> int:
    ap = argparse.ArgumentParser(description="チャンクの埋め込みベクトルを取得する")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="build_index.py が作った search.db")
    ap.add_argument("--cache", default=str(DEFAULT_CACHE))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dim", type=int, default=DEFAULT_DIM)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--limit", type=int, default=0, help="先頭 N 件だけ（動作確認用）")
    ap.add_argument("--estimate", action="store_true", help="件数と概算だけ出して終わる")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"search.db がありません: {db}\n先に python pipeline/build_index.py を実行してください。")
        return 1

    cache = open_cache(Path(args.cache))
    todo = pending_chunks(db, cache, args.model, args.dim)
    if args.limit:
        todo = todo[: args.limit]

    if not todo:
        total = cache.execute(
            "SELECT count(*) FROM vectors WHERE model = ? AND dim = ?", (args.model, args.dim)
        ).fetchone()[0]
        print(f"取得済みです（キャッシュ {total} 件）。")
        return 0

    chars = sum(len(t[:MAX_CHARS]) for _, t in todo)
    print(f"未取得 {len(todo)} 件 / 約 {chars:,} 文字（≒{chars // 1000:,}k トークン）")
    if args.estimate:
        print("--estimate なのでここで終了します。")
        return 0

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("環境変数 GEMINI_API_KEY を設定してください。")
        return 1

    client = EmbeddingClient(api_key, args.model, args.dim)
    done = 0
    try:
        for i in range(0, len(todo), args.batch):
            batch = todo[i : i + args.batch]
            vectors = client.embed_batch([t for _, t in batch])
            cache.executemany(
                "INSERT OR REPLACE INTO vectors (text_hash, model, dim, vec) VALUES (?,?,?,?)",
                [(h, args.model, args.dim, pack(v)) for (h, _), v in zip(batch, vectors)],
            )
            cache.commit()
            done += len(batch)
            print(f"  {done}/{len(todo)}", flush=True)
    finally:
        client.close()
        cache.close()

    print(f"\n{done} 件を {args.cache} に保存しました。")
    print("search.db に反映するには python pipeline/build_index.py を再実行してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
