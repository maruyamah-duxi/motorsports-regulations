"""構造化ドキュメント → HTML."""

from __future__ import annotations

import html
from typing import Any

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
"""


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


def render_html(document: dict[str, Any]) -> str:
    title = document.get("title", document.get("docId", ""))
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="ja"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{_esc(title)}</title>",
        f"<style>{_STYLE}</style>",
        "</head><body><article class=\"doc\">",
        f"<h1>{_esc(title)}</h1>",
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

    if document.get("warnings"):
        parts.append(
            '<p class="warn">この文書には自動変換で完全に読み取れなかった箇所があります。'
            "正式な判断は必ず JAF の原本 PDF をご確認ください。</p>"
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
            parts.append(
                f"<figure>{pagemark}"
                f'<img src="{src}" loading="lazy" alt="{_esc(cap or "図版")}">'
                + (f"<figcaption>{_esc(cap)}</figcaption>" if cap else "")
                + "</figure>"
            )
        elif btype == "table":
            parts.append(f'<div class="tablewrap">{pagemark}{_render_table(b.get("rows") or [])}</div>')

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
