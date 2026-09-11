"""構造化ドキュメント → HTML."""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any
from urllib.parse import quote

# この HTML は /content/<docId>/index.html として単体でも配信される。
# アプリ側の /doc/<docId> と中身が重なるので、canonical で寄せ先を
# 固定する（両方が別ページとして評価されると検索での順位が割れる）。
CANONICAL_ORIGIN = "https://jp.motorsports-regulations.org"
SITE_NAME = "JAF モータースポーツ諸規則ビューア（非公式）"
DESCRIPTION_MAX = 150

_WS = re.compile(r"\s+")
_TRAILING_DATE = re.compile(r"[_\-]\d{8}$")
_CLAUSE_HEAD = re.compile(r"^第[0-9０-９]+[条章編節]")

_STYLE = """
:root{--fg:#111827;--muted:#6b7280;--line:#e5e7eb;--accent:#1d4ed8;--bg:#fff}
@media (prefers-color-scheme:dark){:root{--fg:#e5e7eb;--muted:#9ca3af;--line:#374151;--accent:#93c5fd;--bg:#0f172a}}
body{margin:0;background:var(--bg);color:var(--fg);font-family:"Noto Sans JP",system-ui,sans-serif;line-height:1.9}
.doc{max-width:46rem;margin:0 auto;padding:2rem 1.25rem 6rem}
h1{font-size:1.6rem;line-height:1.5;margin:0 0 .25rem}
.meta{color:var(--muted);font-size:.8rem;margin-bottom:2rem}
h2,h3,h4,h5{line-height:1.6;margin:2.2em 0 .6em;scroll-margin-top:4rem}
h2{font-size:1.25rem;border-left:4px solid var(--accent);padding-left:.6rem}
h3{font-size:1.1rem}
h4{font-size:1rem;color:var(--accent)}
p{margin:0 0 1em;text-align:justify}
figure{margin:1.8em 0;padding:.75rem;border:1px solid var(--line);border-radius:.5rem}
figure img{display:block;width:100%;height:auto}
figcaption{margin-top:.5rem;font-size:.85rem;color:var(--muted);text-align:center}
.tablewrap{overflow-x:auto;margin:1.5em 0}
table{border-collapse:collapse;font-size:.9rem;min-width:100%}
th,td{border:1px solid var(--line);padding:.35em .6em;vertical-align:top}
.pagemark{float:right;font-size:.7rem;color:var(--muted);user-select:none}
nav.toc{border:1px solid var(--line);border-radius:.5rem;padding:1rem 1.25rem;margin-bottom:2.5rem;font-size:.9rem}
nav.toc ol{list-style:none;margin:0;padding:0}
nav.toc a{color:inherit;text-decoration:none}
nav.toc a:hover{color:var(--accent);text-decoration:underline}
nav.toc .l1{padding-left:0;font-weight:700}
nav.toc .l2{padding-left:1rem}
nav.toc .l3{padding-left:2rem}
nav.toc .l4{padding-left:3rem;color:var(--muted)}
.warn{background:#fef3c7;color:#78350f;border-radius:.4rem;padding:.6rem .9rem;font-size:.85rem;margin-bottom:1.5rem}
.src{border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:.4rem;padding:.6rem .9rem;font-size:.8rem;line-height:1.8;color:var(--muted);margin:0 0 2rem}
.src strong{color:var(--fg)}
.docfoot{margin-top:4rem;padding-top:1rem;border-top:1px solid var(--line);font-size:.78rem;line-height:1.9;color:var(--muted)}
"""


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def _norm(text: Any) -> str:
    """全角英数・全角空白をならす（利用者は半角で検索する）."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", str(text or ""))).strip()


def _plain_title(title: Any) -> str:
    return _TRAILING_DATE.sub("", _norm(title))


def description(document: dict[str, Any]) -> str:
    """規則ごとに違う meta description を作る.

    既定文を使い回すと 160 件が同じ説明になる。条見出しを並べておくと
    「第12条 安全ベルト」のような検索で、どの規則のどこに何があるかが
    検索結果の時点で分かる。
    """
    heads: list[str] = []
    seen: set[str] = set()
    for item in document.get("toc") or []:
        text = _norm(item.get("text"))
        if not text or text in seen or not _CLAUSE_HEAD.match(text):
            continue
        seen.add(text)
        heads.append(text)
        if len(heads) >= 12:
            break

    where = "／".join(
        x for x in (_norm(document.get("section")), _norm(document.get("group"))) if x
    )
    lead = _plain_title(document.get("title") or document.get("docId"))
    if where:
        lead += f"（{where}）"
    lead += "の全文。"
    tail = (
        "／".join(heads)
        if heads
        else "JAF 公開 PDF を条文単位で検索できる非公式アーカイブ。"
    )
    out = lead + tail
    if len(out) > DESCRIPTION_MAX:
        out = out[: DESCRIPTION_MAX - 1].rstrip("／、。 ") + "…"
    return out


def render_html(document: dict[str, Any]) -> str:
    title = document.get("title", document.get("docId", ""))
    doc_id = document.get("docId") or ""
    canonical = f"{CANONICAL_ORIGIN}/doc/{quote(doc_id, safe='')}" if doc_id else ""
    desc = description(document)
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="ja"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        # この HTML は SPA の外で配信されるので、アイコンも自前で指す
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">',
        '<link rel="icon" href="/favicon.ico" sizes="32x32">',
        f"<title>{_esc(_plain_title(title))}｜{_esc(SITE_NAME)}</title>",
        f'<meta name="description" content="{_esc(desc)}">',
        # 検索での寄せ先はアプリ側の /doc/<docId>。あちらは同じ本文を
        # サーバ側で埋め込んだうえで、検索と AI 質問も付く（server/seo.py）。
        *(
            [
                f'<link rel="canonical" href="{_esc(canonical)}">',
                f'<meta property="og:url" content="{_esc(canonical)}">',
            ]
            if canonical
            else []
        ),
        '<meta property="og:type" content="article">',
        f'<meta property="og:site_name" content="{_esc(SITE_NAME)}">',
        '<meta property="og:locale" content="ja_JP">',
        f'<meta property="og:title" content="{_esc(_plain_title(title))}">',
        f'<meta property="og:description" content="{_esc(desc)}">',
        f"<style>{_STYLE}</style>",
        "</head><body><article class=\"doc\">",
        f"<h1>{_esc(_plain_title(title))}</h1>",
    ]

    meta_bits = [
        document.get("section"),
        document.get("group"),
        f"アップロード日 {document['uploadDate']}" if document.get("uploadDate") else None,
        f"{document.get('pageCount')}ページ" if document.get("pageCount") else None,
    ]
    parts.append(
        '<p class="meta">'
        + " ／ ".join(_esc(b) for b in meta_bits if b)
        + (
            f' ／ <a href="{_esc(document["pdfUrl"])}" rel="nofollow">JAF 原本 PDF</a>'
            if document.get("pdfUrl")
            else ""
        )
        + "</p>"
    )

    # この HTML は /content/<docId>/index.html として単体でも配信され、
    # SPA のヘッダもフッタも付かない。検索エンジン経由でここへ直接来る
    # 読者にも出典の注意書きが届くよう、ページ自身に持たせる。
    pdf_link = (
        f'<a href="{_esc(document["pdfUrl"])}" rel="nofollow">JAF の原本 PDF</a>'
        if document.get("pdfUrl")
        else '<a href="https://motorsports.jaf.or.jp/regulations/information"'
        ' rel="nofollow">JAF のサイト</a>'
    )
    parts.append(
        '<p class="src"><strong>JAF の公式サイトではありません。</strong>'
        "JAF が公開する PDF を自動変換した非公式の検索用アーカイブです。"
        f"記載内容は必ず {pdf_link} で出典をご確認ください。"
        + (
            f' <a href="{_esc(canonical)}">検索と AI 質問つきのビューアで開く</a>'
            if canonical
            else ""
        )
        + "</p>"
    )

    if document.get("warnings"):
        parts.append(
            '<p class="warn">この文書には自動変換で完全に読み取れなかった箇所があります。</p>'
        )

    toc = document.get("toc") or []
    if len(toc) >= 3:
        parts.append('<nav class="toc"><ol>')
        for item in toc:
            lvl = min(4, int(item.get("level", 3)))
            parts.append(
                f'<li class="l{lvl}"><a href="#{_esc(item["id"])}">{_esc(item["text"])}</a></li>'
            )
        parts.append("</ol></nav>")

    last_page = None
    for b in document.get("blocks", []):
        page = b.get("page")
        pagemark = ""
        if page != last_page:
            pagemark = f'<span class="pagemark" id="p{page}">P.{page}</span>'
            last_page = page

        btype = b.get("type")
        if btype == "heading":
            lvl = min(5, max(2, int(b.get("level", 3)) + 1))
            parts.append(
                f'<h{lvl} id="{_esc(b.get("id",""))}">{pagemark}{_esc(b.get("text"))}</h{lvl}>'
            )
        elif btype == "paragraph":
            parts.append(f"<p>{pagemark}{_esc(b.get('text'))}</p>")
        elif btype == "caption":
            continue  # figure 側に取り込み済み
        elif btype == "figure":
            src = _esc(b.get("asset", ""))
            cap = b.get("caption")
            # 縦横比を先に伝えて、画像読み込みによる本文のずれを防ぐ
            bbox = b.get("bbox") or [0, 0, 0, 0]
            ratio = (bbox[2] - bbox[0]) / (bbox[3] - bbox[1]) if bbox[3] - bbox[1] else 0
            style = f' style="aspect-ratio:{ratio:.4f}"' if ratio else ""
            parts.append(
                f"<figure>{pagemark}"
                f'<img src="{src}" loading="lazy" alt="{_esc(cap or "図版")}"{style}>'
                + (f"<figcaption>{_esc(cap)}</figcaption>" if cap else "")
                + "</figure>"
            )
        elif btype == "table":
            parts.append(f'<div class="tablewrap">{pagemark}{_render_table(b.get("rows") or [])}</div>')

    foot_bits = [
        f"原本: {_esc(title)}（JAF）",
        f"自動変換 {_esc(str(document['convertedAt'])[:10])}"
        if document.get("convertedAt")
        else None,
        f"パイプライン {_esc(document['pipelineVersion'])}"
        if document.get("pipelineVersion")
        else None,
    ]
    parts.append(
        '<footer class="docfoot">'
        "自動変換のため誤りが含まれる可能性があります。"
        f"競技における判断は必ず {pdf_link} をご確認ください。<br>"
        + " ／ ".join(b for b in foot_bits if b)
        + ' ／ <a href="/">検索して読む</a>'
        + "</footer>"
    )
    parts.append("</article></body></html>")
    return "\n".join(parts)


def _looks_like_header(rows: list[list[Any]]) -> bool:
    """1 行目が見出し行か推定する.

    JAF の表は見出し行を持たないものが多く、無条件に <th> にすると
    本文の 1 行目が太字になってしまう。「1 行目が全て短く、かつ他の行に
    長いセルがある」ときだけ見出しとみなす。
    """
    if len(rows) < 2:
        return False
    first = [str(c or "") for c in rows[0]]
    if not all(first) or max((len(c) for c in first), default=0) > 12:
        return False
    rest_max = max((len(str(c or "")) for r in rows[1:] for c in r), default=0)
    return rest_max > 20


def _render_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    header = _looks_like_header(rows)
    out = ["<table>"]
    for i, row in enumerate(rows):
        tag = "th" if (header and i == 0) else "td"
        cells = "".join(f"<{tag}>{_esc(c)}</{tag}>" for c in row)
        out.append(f"<tr>{cells}</tr>")
    out.append("</table>")
    return "".join(out)
