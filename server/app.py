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
from typing import Any, Iterator
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import MutableHeaders

from . import rag, seo
from .excerpt import excerpt as _excerpt

ROOT = Path(os.environ.get("APP_ROOT", Path(__file__).resolve().parent.parent))
DB_PATH = Path(os.environ.get("SEARCH_DB", ROOT / "data" / "search.db"))
CONTENT_DIR = Path(os.environ.get("CONTENT_DIR", ROOT / "content"))
DIST_DIR = Path(os.environ.get("DIST_DIR", ROOT / "dist"))
# canonical に使う正本の URL。既定は独自ドメイン（Cloud Run の
# *.run.app にも同じ中身が出るため、寄せ先を固定しないと評価が割れる）。
SITE_ORIGIN = os.environ.get("SITE_ORIGIN", seo.DEFAULT_ORIGIN)

MIN_TRIGRAM_LEN = 3
MAX_LIMIT = 100

MAX_QUESTION_CHARS = 400

# ベクトルは起動時に一度だけ読み込む（約 27MB）
_vectors = rag.VectorIndex(str(DB_PATH))
_rate_limiter = rag.RateLimiter(capacity=10, refill_seconds=30.0)
_answer_cache = rag.AnswerCache()
_http = httpx.Client(timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0))
_seo = seo.Seo(DB_PATH, CONTENT_DIR, DIST_DIR, SITE_ORIGIN)

# python:3.12-slim には .webp の MIME 定義が無く、図版が text/plain で配られる。
# ブラウザは中身を見て画像と判断してくれるが、nosniff を効かせた環境では
# 表示されなくなるので明示しておく。
mimetypes.add_type("image/webp", ".webp")

app = FastAPI(title="JAF Motorsports Regulations API", docs_url="/api/docs", redoc_url=None)


class _GZipExceptSSE(GZipMiddleware):
    """gzip をかける。ただし /api/ask（SSE）は素通しする.

    プリレンダを入れたので /doc/<docId> の HTML は数十〜250KB になる。
    圧縮しないと表示が遅く、Core Web Vitals にも効く。一方 Starlette の
    GZipMiddleware はストリーミング応答をチャンクごとに握るため、SSE の
    トークンが手元に溜まって流れなくなる。パスで分ける。
    """

    async def __call__(self, scope, receive, send):  # type: ignore[override]
        if scope.get("type") == "http" and scope.get("path", "").startswith("/api/ask"):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app.add_middleware(_GZipExceptSSE, minimum_size=1024)


class _NoIndexHeader:
    """検索露出を切っている間、全応答に X-Robots-Tag を足す.

    `seo.SEARCH_INDEXING` が False のときだけ効く。HTML には
    `<meta name="robots">` も入れているが、こちらは
    `/content/<docId>/index.html`（StaticFiles が配る）や図版にも掛かる。

    ASGI 層でヘッダだけ触る。`@app.middleware("http")`
    （BaseHTTPMiddleware）はストリーミング応答を握るので、/api/ask の SSE を
    壊さないためにこの形にしている。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope.get("type") != "http" or seo.SEARCH_INDEXING:
            await self.app(scope, receive, send)
            return

        async def _send(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Robots-Tag"] = "noindex, nofollow"
            await send(message)

        await self.app(scope, receive, _send)


app.add_middleware(_NoIndexHeader)


def _json_list(value: Any) -> list[Any]:
    """JSON の配列を格納した列を読む。壊れていても落とさない."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


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
def _query_terms(q: str) -> list[str]:
    """ユーザ入力を検索語に切る（FTS の式づくりと抜粋の両方で使う）."""
    # 大文字小文字は trigram トークナイザが吸収するので NFKC だけかける
    normalized = _FTS_UNSAFE.sub("", unicodedata.normalize("NFKC", q))
    # 空白区切りをそのまま使い、区切りが無ければ文字種の切れ目で分ける。
    # 「ロールケージ 溶接」も「ロールケージの溶接」も引けるようにする。
    terms = [p for p in normalized.split() if len(p) >= MIN_TRIGRAM_LEN]
    if not terms:
        terms = [t for t in rag.extract_terms(normalized) if len(t) >= MIN_TRIGRAM_LEN]
    return terms


def _fts_query(q: str) -> str:
    """ユーザ入力を FTS5 のフレーズ検索式にする.

    trigram トークナイザではフレーズ（"…"）が部分一致検索になる。
    空白区切りの語は AND で繋ぐ。
    """
    return " AND ".join(f'"{t}"' for t in _query_terms(q))


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
                     ch.page_start, ch.page_end, ch.text, ch.figures,
                     d.title, d.section, d.grp, d.pdf_url, d.upload_date,
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
            # 「どの規則に何件あるか」。装備品を調べるときは散らばり自体が
            # 知りたい情報（「安全ベルト」は 29 文書 130 箇所）なので、
            # 表示中のページではなく**全ヒット**を集計する。
            facet_sql = (
                "SELECT ch.doc_id, d.title, d.section, d.grp, d.pdf_url, count(*) AS n"
                " FROM chunks_fts JOIN chunks ch ON ch.id = chunks_fts.rowid"
                " JOIN docs d ON d.doc_id = ch.doc_id"
                f" WHERE chunks_fts MATCH :match{where_doc}"
                " GROUP BY ch.doc_id ORDER BY n DESC, d.section, d.title"
            )
        else:
            # 2 文字以下は trigram で引けないので LIKE にフォールバック
            sql = f"""
              SELECT ch.doc_id, ch.anchor, ch.heading, ch.heading_path, ch.clause,
                     ch.page_start, ch.page_end, ch.text, ch.figures,
                     d.title, d.section, d.grp, d.pdf_url, d.upload_date,
                     0 AS score
              FROM chunks ch JOIN docs d ON d.doc_id = ch.doc_id
              WHERE ch.text_norm LIKE :like{where_doc} ESCAPE '\\'
              LIMIT :limit OFFSET :offset
            """
            params["like"] = f"%{unicodedata.normalize('NFKC', q)}%"
            count_sql = f"SELECT count(*) FROM chunks ch WHERE ch.text_norm LIKE :like{where_doc} ESCAPE '\\'"
            facet_sql = (
                "SELECT ch.doc_id, d.title, d.section, d.grp, d.pdf_url, count(*) AS n"
                " FROM chunks ch JOIN docs d ON d.doc_id = ch.doc_id"
                f" WHERE ch.text_norm LIKE :like{where_doc} ESCAPE '\\'"
                " GROUP BY ch.doc_id ORDER BY n DESC, d.section, d.title"
            )

        rows = con.execute(sql, params).fetchall()
        total = con.execute(count_sql, params).fetchone()[0]
        facets = con.execute(facet_sql, params).fetchall()
    finally:
        con.close()

    terms = _query_terms(q) or [q]

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
            "snippet": _excerpt(r["text"], terms),
            "uploadDate": r["upload_date"],
            "pdfUrl": r["pdf_url"],
            # 原本 PDF の**該当ページ**。ブラウザ内の PDF ビューアはこの
            # 指定を見てそのページを開く（page_start は 1 起点で、8,778
            # チャンクすべてがページ数の範囲内であることを確認済み）。
            # スマホではダウンロードになってページ指定が効かないことがある。
            "pdfPageUrl": (
                f"{r['pdf_url']}#page={r['page_start']}"
                if r["pdf_url"] and r["page_start"]
                else r["pdf_url"]
            ),
            # この条に属する図版だけ。全文を出さない代わりに、拾った条文と
            # 一緒に図を見て判断できるようにする。
            "figures": [
                {
                    "url": f"/content/{quote(r['doc_id'])}/{quote(str(f.get('asset')))}",
                    "caption": f.get("caption"),
                    "page": f.get("page"),
                }
                for f in _json_list(r["figures"])
                if isinstance(f, dict) and f.get("asset")
            ],
            # アプリ内の該当箇所への直リンク
            "url": f"/doc/{quote(r['doc_id'])}"
            + (f"#{quote(r['anchor'])}" if r["anchor"] else f"#p{r['page_start']}"),
            # 単体で読める静的 HTML（アプリを介さずに参照したいとき用）
            "staticUrl": f"/content/{quote(r['doc_id'])}/index.html"
            + (f"#{quote(r['anchor'])}" if r["anchor"] else f"#p{r['page_start']}"),
        }
        for r in rows
    ]
    return {
        "query": q,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
        # 全ヒットを規則ごとに集計したもの（散らばりを最初に見せる）
        "byDoc": [
            {
                "docId": f["doc_id"],
                "title": f["title"],
                "section": f["section"],
                "group": f["grp"],
                "pdfUrl": f["pdf_url"],
                "count": f["n"],
            }
            for f in facets
        ],
    }


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


# docId はそのままファイルパスに使うので、素性を厳しく確かめる。
# 文字種の制限だけでは ".." が通ってしまい content/ の外に出られる
# （区切り文字は弾けるが、"." と "-" は docId に必要なので許している）。
_DOC_ID = re.compile(r"[0-9A-Za-z._\-]+")


def _safe_doc_id(value: str) -> str:
    if not _DOC_ID.fullmatch(value) or ".." in value or value in (".", ""):
        raise HTTPException(400, "不正な docId です")
    return value


@app.get("/api/documents/{doc_id}")
def document(doc_id: str) -> JSONResponse:
    doc_id = _safe_doc_id(doc_id)
    path = CONTENT_DIR / doc_id / "document.json"
    if not path.exists():
        raise HTTPException(404, "見つかりません")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/documents/{doc_id}/history")
def document_history(doc_id: str) -> dict[str, Any]:
    """更新履歴と、同じ規則の別年度版。

    JAF は年度が変わると別ファイルとして公開するため、こちらでは別文書に
    なる。系列キー（`jafreg/series.py`）で束ねて相互リンクを出せるように
    する。履歴の日付は **JAF の掲載日**で、こちらが検出した日ではない。
    """
    doc_id = _safe_doc_id(doc_id)

    con = _connect()
    try:
        row = con.execute(
            "SELECT series, edition, history, diffs, announcements"
            " FROM docs WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "見つかりません")

        try:
            events = json.loads(row["history"] or "[]")
        except json.JSONDecodeError:
            events = []
        try:
            diffs = json.loads(row["diffs"] or "[]")
        except json.JSONDecodeError:
            diffs = []
        try:
            notices = json.loads(row["announcements"] or "[]")
        except json.JSONDecodeError:
            notices = []

        editions: list[dict[str, Any]] = []
        if row["series"]:
            editions = [
                {
                    "docId": e["doc_id"],
                    "title": e["title"],
                    "edition": e["edition"],
                    "uploadDate": e["upload_date"],
                    "current": e["doc_id"] == doc_id,
                }
                for e in con.execute(
                    "SELECT doc_id, title, edition, upload_date FROM docs"
                    " WHERE series = ? ORDER BY edition, doc_id",
                    (row["series"],),
                ).fetchall()
            ]
    finally:
        con.close()

    return {
        "docId": doc_id,
        "series": row["series"],
        "edition": row["edition"],
        "events": events,
        # 1 件（自分だけ）のときは相互リンクを出す必要がない
        "editions": editions if len(editions) > 1 else [],
        # 条単位の改正差分（本体は下の /diff/{base_doc_id}）
        "diffs": diffs,
        # JAF の公示。対比表があれば JAF 自身の新旧対照へ案内できる。
        "announcements": notices,
    }


@app.get("/api/documents/{doc_id}/diff/{base_doc_id}")
def document_diff(doc_id: str, base_doc_id: str) -> JSONResponse:
    """条単位の改正差分。

    本体は変換時ではなく `build_diffs.py` が作り、`content/<docId>/` に
    置いてある。ここではそれをそのまま返す（サーバ側で difflib を回すと
    数百ページの規則で待たされるため、算出はビルド時に済ませる）。
    """
    doc_id = _safe_doc_id(doc_id)
    base_doc_id = _safe_doc_id(base_doc_id)

    # previous.json との差分は "previous" という名前で置いてある
    name = "diff-previous.json" if base_doc_id == "previous" else f"diff-{base_doc_id}.json"
    path = CONTENT_DIR / doc_id / name
    if not path.exists():
        raise HTTPException(404, "この組み合わせの差分はありません")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


def _optional_counts() -> dict[str, int]:
    """任意データが実際にイメージに入っているかを数える。

    `.dockerignore` でファイル名を間違えて公示が 0 件になった事故があった。
    ビルド時に黙って欠けても気づけるよう、1 回の curl で確かめられるように
    しておく。DB が無い・列が無い場合は 0 を返して健全性チェック自体は通す。
    """
    out = {"history": 0, "diffs": 0, "announcements": 0}
    if not DB_PATH.exists():
        return out
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            for col in out:
                out[col] = con.execute(
                    f"SELECT count(*) FROM docs"
                    f" WHERE {col} IS NOT NULL AND {col} NOT IN ('', '[]')"
                ).fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        pass  # 古いスキーマなら 0 のまま
    return out


def _health() -> dict[str, Any]:
    return {
        "ok": True,
        "searchDb": DB_PATH.exists(),
        "content": CONTENT_DIR.exists(),
        "dist": DIST_DIR.exists(),
        "vectors": _vectors.count,
        # 何が焼き込まれているか（0 のものはビルドコンテキストから漏れている）
        "docsWith": _optional_counts(),
        # 検索エンジン向け。sitemap が 1 件（トップだけ）なら docs テーブルを
        # 読めていない＝規則の URL がクローラに一切届かない状態。
        "seo": {
            "origin": SITE_ORIGIN,
            "sitemapUrls": _seo.sitemap_xml().count("<loc>"),
            # False の間は robots.txt が全面 Disallow で、全応答に noindex が付く
            "indexing": seo.SEARCH_INDEXING,
        },
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
# AI への質問（RAG）
# ---------------------------------------------------------------------------


class HistoryTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(max_length=4000)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=MAX_QUESTION_CHARS)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=10)


def _data_version() -> str:
    """search.db の生成時刻。規則が更新されるとキャッシュを自動で無効化する."""
    global _data_version_cached
    if _data_version_cached is None:
        try:
            con = _connect()
            try:
                row = con.execute("SELECT value FROM meta WHERE key = 'builtAt'").fetchone()
            finally:
                con.close()
            _data_version_cached = row[0] if row else "unknown"
        except Exception:
            _data_version_cached = "unknown"
    return _data_version_cached


_data_version_cached: str | None = None


@app.get("/api/ask/status")
def ask_status() -> dict[str, Any]:
    """AI 回答が使える状態かをフロントに知らせる."""
    has_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    return {
        "available": has_key and DB_PATH.exists(),
        "hasApiKey": has_key,
        "vectors": _vectors.count,
        "hybrid": _vectors.ready,
        "chatModel": rag.CHAT_MODEL,
        "embedModel": rag.EMBED_MODEL,
        "cache": _answer_cache.stats(),
    }


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/ask")
def ask(body: AskRequest, request: Request) -> StreamingResponse:
    client_ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    client_ip = client_ip or (request.client.host if request.client else "unknown")
    if not _rate_limiter.allow(client_ip):
        raise HTTPException(429, "短時間に多くの質問を受け付けました。少し待ってからお試しください。")

    question = body.question.strip()
    history = [{"role": t.role, "text": t.text} for t in body.history]

    # 会話の続きは文脈で答えが変わるのでキャッシュしない。
    # 連打されるのは単発の質問なので、そこだけ効かせれば十分。
    cache_key = rag.AnswerCache.key(question, _data_version()) if not history else None

    NO_SOURCE_MESSAGE = (
        "参考資料の中に該当する条文が見つかりませんでした。"
        "語を変えて（例:「安全ベルト」→「ベルト」）お試しいただくか、"
        "上の検索窓から全文検索をお使いください。"
    )

    def generate() -> Iterator[str]:
        if cache_key:
            hit = _answer_cache.get(cache_key)
            if hit is not None:
                # 検索も生成も呼ばずに返す（費用ゼロ）
                yield _sse("sources", hit.sources)
                yield _sse("delta", hit.answer)
                yield _sse("done", {"ok": True, "sources": len(hit.sources), "cached": True})
                return

        con = None
        try:
            con = _connect()
            sources = rag.retrieve(
                con, question, _vectors, _http, unicodedata.normalize("NFKC", question)
            )
            source_dicts = [s.to_dict() for s in sources]
            yield _sse("sources", source_dicts)

            if not sources:
                yield _sse("delta", NO_SOURCE_MESSAGE)
                yield _sse("done", {"ok": True, "sources": 0, "cached": False})
                # 根拠 0 件の連打がいちばん止めたいケースなので、これも覚える
                if cache_key:
                    _answer_cache.put(cache_key, rag.CachedAnswer([], NO_SOURCE_MESSAGE))
                return

            collected: list[str] = []
            for delta in rag.stream_answer(question, sources, history, _http):
                collected.append(delta)
                yield _sse("delta", delta)
            yield _sse("done", {"ok": True, "sources": len(sources), "cached": False})

            answer = "".join(collected).strip()
            if cache_key and answer:  # 失敗・空応答は覚えない
                _answer_cache.put(cache_key, rag.CachedAnswer(source_dicts, answer))
        except rag.RagUnavailable as exc:
            yield _sse("error", {"message": f"AI 回答は現在利用できません（{exc}）。"})
        except Exception as exc:  # noqa: BLE001 - 失敗の中身は利用者に見せない
            print(f"[ask] {type(exc).__name__}: {exc}", flush=True)
            yield _sse("error", {"message": "回答の生成に失敗しました。時間をおいてお試しください。"})
        finally:
            if con is not None:
                con.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# 検索エンジン向け（catch-all より前に登録する）
# ---------------------------------------------------------------------------


@app.get("/robots.txt", include_in_schema=False)
def robots_txt() -> PlainTextResponse:
    return PlainTextResponse(
        _seo.robots_txt(), headers={"Cache-Control": "public, max-age=3600"}
    )


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml() -> Response:
    return Response(
        _seo.sitemap_xml(),
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ---------------------------------------------------------------------------
# 静的ファイル（API より後に登録する。Starlette は登録順にマッチする）
# ---------------------------------------------------------------------------

# 規則の HTML と図版
if CONTENT_DIR.exists():
    app.mount("/content", StaticFiles(directory=CONTENT_DIR, html=True), name="content")

# HTML に効かせるキャッシュ指定。
#
# ここを `max-age=300` にしていたら「デプロイしたのに古いサイトが出る」に
# なった。HTML にはビルドごとに変わるアセットのファイル名が書かれているので、
# **HTML を寝かせると古いアセット名を指したままになる**。毎回問い合わせさせ、
# 中身が同じなら 304 で済ませる（no-cache は「使うな」ではなく「毎回確かめろ」）。
HTML_CACHE = "no-cache"
# 逆に /assets/ の中身はファイル名にハッシュが入っていて、変わったら名前も
# 変わる。1 年間そのまま使ってよい。
ASSET_CACHE = "public, max-age=31536000, immutable"


class _ImmutableAssets(StaticFiles):
    """ハッシュ付きアセットに長期キャッシュを付ける StaticFiles."""

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = ASSET_CACHE
        return response


# ビルド済み SPA。フロントは History API でルーティングするので、
# 実ファイルが無いパスには index.html を返す（/doc/<id> の直リンク・リロード対策）。
if DIST_DIR.exists():
    assets = DIST_DIR / "assets"
    if assets.exists():
        app.mount("/assets", _ImmutableAssets(directory=assets), name="assets")

    _INDEX = DIST_DIR / "index.html"

    # HEAD も受ける。@app.get だけだとクローラや監視の HEAD が 405 になる。
    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(full_path: str) -> Response:
        if full_path:
            candidate = (DIST_DIR / full_path).resolve()
            root = DIST_DIR.resolve()
            if candidate.is_file() and root in candidate.parents:
                # dist 直下の実ファイル（favicon など）。HTML だけは寝かせない。
                cache = HTML_CACHE if candidate.suffix == ".html" else ASSET_CACHE
                return FileResponse(candidate, headers={"Cache-Control": cache})
        # 規則ごとの title / description / canonical と本文プリレンダを
        # 差し込んだ HTML を返す。扱わないパスは None が返るので素の
        # index.html（従来どおり）。
        page = _seo.page("/" + full_path)
        if page is not None:
            return HTMLResponse(page, headers={"Cache-Control": HTML_CACHE})
        return FileResponse(_INDEX, headers={"Cache-Control": HTML_CACHE})
