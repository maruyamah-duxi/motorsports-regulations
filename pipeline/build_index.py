#!/usr/bin/env python3
"""content/*/document.json から全文検索用の SQLite を作る.

日本語の全文検索はクライアント側に持たせるとインデックスが重くなるため、
Cloud Run のイメージに焼き込む SQLite を採用する。

トークナイザは **FTS5 の trigram**。分かち書きが要らず、「安全ベルト」でも
「全ベル」でも当たるので、法令テキストの部分一致検索と相性が良い。
（trigram は 3 文字以上のクエリが前提。2 文字以下は LIKE にフォールバックする。）

チャンクは **見出し単位**。RAG でもそのまま使えるよう、規則名・見出し階層・
ページ・アンカーをメタデータとして持たせる。

    python pipeline/build_index.py --content content --out data/search.db
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Iterator

MAX_CHUNK_CHARS = 1500
MIN_TRIGRAM_LEN = 3


def normalize(text: str) -> str:
    """検索キーと本文を同じ土俵に載せる.

    NFKC だけをかけて全角英数・半角カナの揺れを吸収する。
    大文字小文字は FTS5 の trigram トークナイザが既定で無視するので
    casefold はしない。抜粋（snippet）はこの正規化後の文字列から作られる
    ため、小文字化すると「ＦＩＡ」が「fia」と表示されてしまう。
    """
    return unicodedata.normalize("NFKC", text)


SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;

CREATE TABLE docs (
  doc_id      TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  source      TEXT,
  section     TEXT,
  grp         TEXT,
  upload_date TEXT,
  pdf_url     TEXT,
  page_count  INTEGER,
  chars       INTEGER,
  figures     INTEGER,
  tables      INTEGER,
  -- 年度版をまとめる系列キーと版の呼び名（jafreg/series.py）
  series      TEXT,
  edition     TEXT,
  -- 更新履歴のイベント配列（build_history.py が作る JSON）
  history     TEXT,
  -- 条単位の改正差分の一覧（build_diffs.py が作る JSON。本体は content/ 側）
  diffs       TEXT,
  -- JAF の公示（link_announcements.py が系列に紐づけたもの）。
  -- 対比表 PDF があれば JAF 自身の新旧対照へ案内できる。
  announcements TEXT
);
CREATE INDEX idx_docs_series ON docs(series);

CREATE TABLE chunks (
  id           INTEGER PRIMARY KEY,
  doc_id       TEXT NOT NULL REFERENCES docs(doc_id),
  anchor       TEXT,
  heading      TEXT,
  heading_path TEXT,
  clause       TEXT,
  page_start   INTEGER,
  page_end     INTEGER,
  part         INTEGER DEFAULT 0,
  text         TEXT NOT NULL,
  -- 検索用に NFKC で正規化したもの。全角英数／半角カナの揺れを吸収する
  -- （「ＲＲＮ」でも「RRN」でも当たる）。大文字小文字は trigram が吸収する。
  text_norm    TEXT NOT NULL,
  -- 埋め込みベクトルのキャッシュキー（本文が変わらない限り再取得しない）
  text_hash    TEXT NOT NULL,
  -- float32 のリトルエンディアン配列。build_embeddings.py が埋める。
  vec          BLOB
);
CREATE INDEX idx_chunks_hash ON chunks(text_hash);
CREATE INDEX idx_chunks_doc ON chunks(doc_id);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
  text_norm,
  content='chunks',
  content_rowid='id',
  tokenize='trigram'
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

# 埋め込みキャッシュ（data/embeddings.sqlite）。search.db は毎回作り直すが、
# ベクトルの取得には API 費用と時間がかかるので本文ハッシュで持ち回す。
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


def text_hash(text: str) -> str:
    """埋め込みキャッシュのキー。本文が 1 文字でも変われば別物になる。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def iter_chunks(doc: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """見出し単位でチャンクに切る."""
    stack: list[tuple[int, str]] = []  # (level, text)
    current: dict[str, Any] | None = None

    def flush() -> Iterator[dict[str, Any]]:
        if current is None:
            return
        body = "\n".join(current["lines"]).strip()
        if not body:
            return
        head = current["heading"]
        prefix = f"{head}\n" if head else ""
        # 長すぎるチャンクは分割し、どの部分にも見出しを残す
        budget = MAX_CHUNK_CHARS - len(prefix)
        parts = [body[i : i + budget] for i in range(0, len(body), budget)] or [""]
        for i, part in enumerate(parts):
            yield {
                "anchor": current["anchor"],
                "heading": head,
                "heading_path": current["path"],
                "clause": current["clause"],
                "page_start": current["page_start"],
                "page_end": current["page_end"],
                "part": i,
                "text": prefix + part,
            }

    for b in doc.get("blocks", []):
        btype = b.get("type")
        if btype == "heading":
            yield from flush()
            level = int(b.get("level") or 3)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, b.get("text", "")))
            current = {
                "anchor": b.get("id"),
                "heading": b.get("text", ""),
                "path": " > ".join(t for _, t in stack),
                "clause": b.get("clause"),
                "page_start": b.get("page"),
                "page_end": b.get("page"),
                "lines": [],
            }
            continue

        if current is None:
            current = {
                "anchor": None,
                "heading": "",
                "path": "",
                "clause": None,
                "page_start": b.get("page"),
                "page_end": b.get("page"),
                "lines": [],
            }

        if btype in ("paragraph", "caption"):
            current["lines"].append(b.get("text", ""))
        elif btype == "table":
            for row in b.get("rows") or []:
                current["lines"].append(" | ".join(str(c or "") for c in row))
        elif btype == "figure":
            if b.get("caption"):
                current["lines"].append(b["caption"])
        current["page_end"] = b.get("page", current["page_end"])

    yield from flush()


def attach_cached_vectors(con: sqlite3.Connection, cache_path: Path, model: str, dim: int) -> int:
    """埋め込みキャッシュから search.db へベクトルを流し込む."""
    if not cache_path.exists():
        return 0
    con.execute("ATTACH DATABASE ? AS cache", (str(cache_path),))
    try:
        cur = con.execute(
            "UPDATE chunks SET vec = ("
            "  SELECT v.vec FROM cache.vectors v"
            "  WHERE v.text_hash = chunks.text_hash AND v.model = ? AND v.dim = ?"
            ") WHERE vec IS NULL",
            (model, dim),
        )
        con.commit()
        filled = con.execute("SELECT count(*) FROM chunks WHERE vec IS NOT NULL").fetchone()[0]
        return filled
    finally:
        con.execute("DETACH DATABASE cache")


def load_history(path: Path | None) -> dict[str, dict[str, Any]]:
    """build_history.py が作った履歴を読む。無ければ空（履歴なしで動く）."""
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("docs") or {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  履歴を読めませんでした（履歴なしで続けます）: {exc}")
        return {}


def load_diffs(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    """build_diffs.py が作った差分の一覧を読む。本体は content/ 側にある."""
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("docs") or {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  差分の一覧を読めませんでした（差分なしで続けます）: {exc}")
        return {}


def load_announcements(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    """link_announcements.py が作った「系列 → 公示」を読む."""
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("series") or {}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  公示の紐づけを読めませんでした（公示なしで続けます）: {exc}")
        return {}


def build(
    content_dir: Path,
    out_path: Path,
    cache_path: Path | None = None,
    model: str = "gemini-embedding-001",
    dim: int = 768,
    history_path: Path | None = None,
    diffs_path: Path | None = None,
    announcements_path: Path | None = None,
) -> dict[str, int]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    con = sqlite3.connect(out_path)
    con.executescript(SCHEMA)

    history_docs = load_history(history_path)
    diff_docs = load_diffs(diffs_path)
    ann_series = load_announcements(announcements_path)
    # docId → 系列キー。履歴から引く（無ければ公示は付かない）
    series_of = {k: (v or {}).get("series") for k, v in history_docs.items()}
    n_docs = n_chunks = 0
    for doc_json in sorted(content_dir.glob("*/document.json")):
        doc = json.loads(doc_json.read_text(encoding="utf-8"))
        doc_id = doc["docId"]
        stats = doc.get("stats") or {}
        con.execute(
            "INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                doc_id,
                doc.get("title") or doc_id,
                doc.get("source"),
                doc.get("section"),
                doc.get("group"),
                doc.get("uploadDate"),
                doc.get("pdfUrl"),
                doc.get("pageCount"),
                stats.get("chars"),
                stats.get("figures"),
                stats.get("tables"),
                (history_docs.get(doc_id) or {}).get("series"),
                (history_docs.get(doc_id) or {}).get("edition"),
                json.dumps(
                    (history_docs.get(doc_id) or {}).get("events") or [],
                    ensure_ascii=False,
                ),
                json.dumps(diff_docs.get(doc_id) or [], ensure_ascii=False),
                json.dumps(
                    ann_series.get(series_of.get(doc_id) or "") or [], ensure_ascii=False
                ),
            ),
        )
        n_docs += 1
        for c in iter_chunks(doc):
            con.execute(
                "INSERT INTO chunks (doc_id, anchor, heading, heading_path, clause,"
                " page_start, page_end, part, text, text_norm, text_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    doc_id,
                    c["anchor"],
                    c["heading"],
                    c["heading_path"],
                    c["clause"],
                    c["page_start"],
                    c["page_end"],
                    c["part"],
                    c["text"],
                    normalize(c["text"]),
                    text_hash(c["text"]),
                ),
            )
            n_chunks += 1

    con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    con.commit()

    vectors = 0
    if cache_path is not None:
        vectors = attach_cached_vectors(con, cache_path, model, dim)

    con.execute(
        "INSERT INTO meta VALUES ('builtAt', datetime('now')), ('docs', ?), ('chunks', ?),"
        " ('embedModel', ?), ('embedDim', ?), ('vectors', ?)",
        (str(n_docs), str(n_chunks), model, str(dim), str(vectors)),
    )
    con.commit()
    con.execute("VACUUM")
    con.close()
    return {"docs": n_docs, "chunks": n_chunks, "vectors": vectors}


def main() -> int:
    ap = argparse.ArgumentParser(description="全文検索用 SQLite を生成する")
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--content", default=str(root / "content"))
    ap.add_argument("--out", default=str(root / "data" / "search.db"))
    ap.add_argument(
        "--embeddings-cache",
        default=str(root / "data" / "embeddings.sqlite"),
        help="埋め込みキャッシュ。あればベクトルを流し込む",
    )
    ap.add_argument(
        "--history",
        default=str(root / "data" / "history.json"),
        help="build_history.py が作った更新履歴。あれば docs に取り込む",
    )
    ap.add_argument(
        "--diffs",
        default=str(root / "data" / "diffs.json"),
        help="build_diffs.py が作った差分の一覧。あれば docs に取り込む",
    )
    ap.add_argument(
        "--announcements",
        default=str(root / "data" / "announcement_links.json"),
        help="link_announcements.py が作った公示の紐づけ",
    )
    ap.add_argument("--embed-model", default="gemini-embedding-001")
    ap.add_argument("--embed-dim", type=int, default=768)
    ap.add_argument(
        "--require-vectors",
        action="store_true",
        help=(
            "マニフェスト（data/embeddings.manifest.json）があるのにベクトルが"
            "足りなければ失敗する。ベクトル無しで気づかず公開する事故を防ぐ"
        ),
    )
    ap.add_argument(
        "--min-vector-coverage",
        type=float,
        default=0.98,
        help="--require-vectors のときに要求するベクトルの充足率",
    )
    args = ap.parse_args()

    content_dir = Path(args.content)
    if not content_dir.exists():
        print(f"content ディレクトリがありません: {content_dir}")
        return 1
    stats = build(
        content_dir,
        Path(args.out),
        cache_path=Path(args.embeddings_cache) if args.embeddings_cache else None,
        model=args.embed_model,
        dim=args.embed_dim,
        history_path=Path(args.history) if args.history else None,
        diffs_path=Path(args.diffs) if args.diffs else None,
        announcements_path=Path(args.announcements) if args.announcements else None,
    )
    size = Path(args.out).stat().st_size
    vec = stats["vectors"]
    note = f" / ベクトル {vec}" if vec else " / ベクトルなし（build_embeddings.py 未実行）"
    print(
        f"{stats['docs']} 文書 / {stats['chunks']} チャンク{note}"
        f" → {args.out} ({size/1024/1024:.1f} MB)"
    )

    if args.require_vectors:
        rc = check_vector_coverage(
            Path(args.embeddings_cache) if args.embeddings_cache else None,
            chunks=stats["chunks"],
            vectors=vec,
            minimum=args.min_vector_coverage,
        )
        if rc:
            return rc
    return 0


def check_vector_coverage(
    cache_path: Path | None, *, chunks: int, vectors: int, minimum: float
) -> int:
    """ベクトルが足りているかを確かめる。

    マニフェストの有無を「このリポジトリはベクトル検索を前提にしている」
    という宣言として読む。宣言が無ければ全文検索だけの構成として通す。
    """
    manifest_path = (
        cache_path.with_name("embeddings.manifest.json")
        if cache_path
        else Path("data/embeddings.manifest.json")
    )
    if not manifest_path.exists():
        return 0  # ベクトルを使わない構成。何も言わない

    if cache_path is None or not cache_path.exists():
        print()
        print(f"エラー: {manifest_path.name} があるのに {cache_path} がありません。")
        print("  埋め込みの実体は GCS にあります。先にこれを実行してください:")
        print("    python pipeline/embeddings_store.py pull")
        return 1

    coverage = (vectors / chunks) if chunks else 0.0
    if coverage < minimum:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        print()
        print(
            f"エラー: ベクトルが {vectors}/{chunks} チャンク"
            f"（{coverage*100:.1f}%）しかありません。"
        )
        print(f"  マニフェストは {manifest.get('rows')} 行 / {manifest.get('updatedAt')} 時点です。")
        print("  規則が改訂されて本文が変わった分の埋め込みが未取得だと思われます:")
        print("    export GEMINI_API_KEY=...")
        print("    python pipeline/build_embeddings.py")
        print("    python pipeline/embeddings_store.py push")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
