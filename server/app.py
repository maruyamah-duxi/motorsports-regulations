"""Cloud Run で動かす配信 + 検索 API.

1 つのコンテナで
  * ビルド済み SPA (dist/)
  * 変換済みの規則 (content/)
  * 全文検索 API (/api/search) — イメージに焼き込んだ SQLite FTS5 (trigram)
を配る。外部データベースは使わないので追加費用も運用対象も増えない。

RAG の /api/ask はこの上に足す（Gemini の API キーはここ＝サーバ側にだけ置く）。
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(os.environ.get("APP_ROOT", Path(__file__).resolve().parent.parent))
DB_PATH = Path(os.environ.get("SEARCH_DB", ROOT / "data" / "search.db"))
CONTENT_DIR = Path(os.environ.get("CONTENT_DIR", ROOT / "content"))
DIST_DIR = Path(os.environ.get("DIST_DIR", ROOT / "dist"))

MIN_TRIGRAM_LEN = 3
MAX_LIMIT = 100

# python:3.12-slim には .webp の MIME 定義が無く、図版が text/plain で配られる。
# ブラウザは中身を見て画像と判断してくれるが、nosniff を効かせた環境では
# 表示されなくなるので明示しておく。
mimetypes.add_type("image/webp", ".webp")

app = FastAPI(title="JAF Motorsports Regulations API", docs_url="/api/docs", redoc_url=None)


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(503, f"検索インデックスがありません: {DB_PATH.name}")
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# 検索
# ---------------------------------------------------------------------------

_FTS_UNSAFE = re.compile(r'["]')

# 抜粋のハイライトは HTML ではなく制御文字で囲んで返す。
# 規則本文には "<" が現れうるので、HTML を組み立てて返すと
# 受け取り側でエスケープの判断が必要になり事故のもとになる。
HIGHLIGHT_START = "\u0001"
HIGHLIGHT_END = "\u0002"


def _fts_query(q: str) -> str:
    """ユーザ入力を FTS5 のフレーズ検索式にする.

    trigram トークナイザではフレーズ（"…"）が部分一致検索になる。
    空白区切りの語は AND で繋ぐ。
    """
    # 大文字小文字は trigram トークナイザが吸収するので NFKC だけかける
    normalized = unicodedata.normalize("NFKC", q)
    terms = [t for t in _FTS_UNSAFE.sub("", normalized).split() if len(t) >= MIN_TRIGRAM_LEN]
    return " AND ".join(f'"{t}"' for t in terms)


@app.get("/api/search")
def search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    doc: str | None = Query(None, description="docId で絞り込む"),
) -> dict[str, Any]:
    con = _connect()
    try:
        where_doc = " AND ch.doc_id = :doc" if doc else ""
        params: dict[str, Any] = {"limit": limit, "offset": offset, "doc": doc}

        match = _fts_query(q)
        if match:
            sql = f"""
              SELECT ch.doc_id, ch.anchor, ch.heading, ch.heading_path, ch.clause,
                     ch.page_start, ch.page_end,
                     d.title, d.section, d.grp, d.pdf_url, d.upload_date,
                     snippet(chunks_fts, 0, char(1), char(2), ' … ', 56) AS snippet,
                     bm25(chunks_fts) AS score
              FROM chunks_fts
              JOIN chunks ch ON ch.id = chunks_fts.rowid
              JOIN docs   d  ON d.doc_id = ch.doc_id
              WHERE chunks_fts MATCH :match{where_doc}
              ORDER BY score
              LIMIT :limit OFFSET :offset
            """
            params["match"] = match
            count_sql = (
                "SELECT count(*) FROM chunks_fts JOIN chunks ch ON ch.id = chunks_fts.rowid"
                f" WHERE chunks_fts MATCH :match{where_doc}"
            )
        else:
            # 2 文字以下は trigram で引けないので LIKE にフォールバック
            sql = f"""
              SELECT ch.doc_id, ch.anchor, ch.heading, ch.heading_path, ch.clause,
                     ch.page_start, ch.page_end,
                     d.title, d.section, d.grp, d.pdf_url, d.upload_date,
                     substr(ch.text, 1, 160) AS snippet, 0 AS score
              FROM chunks ch JOIN docs d ON d.doc_id = ch.doc_id
              WHERE ch.text_norm LIKE :like{where_doc} ESCAPE '\\'
              LIMIT :limit OFFSET :offset
            """
            params["like"] = f"%{unicodedata.normalize('NFKC', q)}%"
            count_sql = f"SELECT count(*) FROM chunks ch WHERE ch.text_norm LIKE :like{where_doc} ESCAPE '\\'"

        rows = con.execute(sql, params).fetchall()
        total = con.execute(count_sql, params).fetchone()[0]
    finally:
        con.close()

    items = [
        {
            "docId": r["doc_id"],
            "title": r["title"],
            "section": r["section"],
            "group": r["grp"],
            "heading": r["heading"],
            "headingPath": r["heading_path"],
            "clause": r["clause"],
            "page": r["page_start"],
            "anchor": r["anchor"],
            "snippet": r["snippet"],
            "uploadDate": r["upload_date"],
            "pdfUrl": r["pdf_url"],
            # アプリ内の該当箇所への直リンク
            "url": f"/doc/{quote(r['doc_id'])}"
            + (f"#{quote(r['anchor'])}" if r["anchor"] else f"#p{r['page_start']}"),
            # 単体で読める静的 HTML（アプリを介さずに参照したいとき用）
            "staticUrl": f"/content/{quote(r['doc_id'])}/index.html"
            + (f"#{quote(r['anchor'])}" if r["anchor"] else f"#p{r['page_start']}"),
        }
        for r in rows
    ]
    return {"query": q, "total": total, "limit": limit, "offset": offset, "items": items}


# ---------------------------------------------------------------------------
# 文書
# ---------------------------------------------------------------------------


@app.get("/api/documents")
def documents() -> dict[str, Any]:
    con = _connect()
    try:
        rows = con.execute(
            "SELECT doc_id, title, source, section, grp, upload_date, pdf_url,"
            " page_count, chars, figures, tables FROM docs"
            " ORDER BY source, section, grp, title"
        ).fetchall()
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    finally:
        con.close()
    return {
        "builtAt": meta.get("builtAt"),
        "count": len(rows),
        "items": [
            {
                "docId": r["doc_id"],
                "title": r["title"],
                "source": r["source"],
                "section": r["section"],
                "group": r["grp"],
                "uploadDate": r["upload_date"],
                "pdfUrl": r["pdf_url"],
                "pageCount": r["page_count"],
                "chars": r["chars"],
                "figures": r["figures"],
                "tables": r["tables"],
            }
            for r in rows
        ],
    }


@app.get("/api/documents/{doc_id}")
def document(doc_id: str) -> JSONResponse:
    if not re.fullmatch(r"[0-9A-Za-z._\-]+", doc_id):
        raise HTTPException(400, "不正な docId です")
    path = CONTENT_DIR / doc_id / "document.json"
    if not path.exists():
        raise HTTPException(404, "見つかりません")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


def _health() -> dict[str, Any]:
    return {
        "ok": True,
        "searchDb": DB_PATH.exists(),
        "content": CONTENT_DIR.exists(),
        "dist": DIST_DIR.exists(),
        "revision": os.environ.get("K_REVISION"),
    }


# Cloud Run（Google Front End）は "/healthz" ちょうどのパスを横取りして
# 自前の 404 を返すため、アプリまで届かない。実測で確認済み:
#   /healthz  → Google の 404 HTML
#   /healthz/ /healthz2 /api/healthz → アプリに到達
# 動作確認には /api/healthz を使う。/healthz はローカル用に残す。
@app.get("/api/healthz")
def api_healthz() -> dict[str, Any]:
    return _health()


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, Any]:
    return _health()


# ---------------------------------------------------------------------------
# 静的ファイル（API より後に登録する。Starlette は登録順にマッチする）
# ---------------------------------------------------------------------------

# 規則の HTML と図版
if CONTENT_DIR.exists():
    app.mount("/content", StaticFiles(directory=CONTENT_DIR, html=True), name="content")

# ビルド済み SPA。フロントは History API でルーティングするので、
# 実ファイルが無いパスには index.html を返す（/doc/<id> の直リンク・リロード対策）。
if DIST_DIR.exists():
    assets = DIST_DIR / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    _INDEX = DIST_DIR / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path:
            candidate = (DIST_DIR / full_path).resolve()
            root = DIST_DIR.resolve()
            if candidate.is_file() and root in candidate.parents:
                return FileResponse(candidate)
        return FileResponse(_INDEX)
