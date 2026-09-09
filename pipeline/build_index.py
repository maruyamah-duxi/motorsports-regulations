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
import json
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Iterator

MAX_CHUNK_CHARS = 1500
MIN_TRIGRAM_LEN = 3


def normalize(text: str) -> str:
    """検索キーと本文を同じ土俵に載せる（NFKC + 小文字化）."""
    return unicodedata.normalize("NFKC", text).casefold()


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
  tables      INTEGER
);

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
  -- 検索用に NFKC + 小文字化したもの。全角英数／半角カナ／英大小の
  -- 揺れを吸収する（「ＲＲＮ」でも「rrn」でも、「ﾍﾞﾙﾄ」でも当たる）。
  text_norm    TEXT NOT NULL
);
CREATE INDEX idx_chunks_doc ON chunks(doc_id);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
  text_norm,
  content='chunks',
  content_rowid='id',
  tokenize='trigram'
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


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


def build(content_dir: Path, out_path: Path) -> dict[str, int]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    con = sqlite3.connect(out_path)
    con.executescript(SCHEMA)

    n_docs = n_chunks = 0
    for doc_json in sorted(content_dir.glob("*/document.json")):
        doc = json.loads(doc_json.read_text(encoding="utf-8"))
        doc_id = doc["docId"]
        stats = doc.get("stats") or {}
        con.execute(
            "INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
            ),
        )
        n_docs += 1
        for c in iter_chunks(doc):
            con.execute(
                "INSERT INTO chunks (doc_id, anchor, heading, heading_path, clause,"
                " page_start, page_end, part, text, text_norm) VALUES (?,?,?,?,?,?,?,?,?,?)",
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
                ),
            )
            n_chunks += 1

    con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    con.execute(
        "INSERT INTO meta VALUES ('builtAt', datetime('now')), ('docs', ?), ('chunks', ?)",
        (str(n_docs), str(n_chunks)),
    )
    con.commit()
    con.execute("VACUUM")
    con.close()
    return {"docs": n_docs, "chunks": n_chunks}


def main() -> int:
    ap = argparse.ArgumentParser(description="全文検索用 SQLite を生成する")
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--content", default=str(root / "content"))
    ap.add_argument("--out", default=str(root / "data" / "search.db"))
    args = ap.parse_args()

    content_dir = Path(args.content)
    if not content_dir.exists():
        print(f"content ディレクトリがありません: {content_dir}")
        return 1
    stats = build(content_dir, Path(args.out))
    size = Path(args.out).stat().st_size
    print(f"{stats['docs']} 文書 / {stats['chunks']} チャンク → {args.out} ({size/1024/1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
