"""検索エンジンに読ませるための HTML 加工と robots / sitemap.

なぜ必要か
----------
フロントは History API のルータで、`/doc/<docId>` はどの規則でも同じ
`dist/index.html`（JS シェル）を返していた。つまり

* 160 件すべてが同一の `<title>` と `<meta description>`
* 本文は JS を実行して `/api/documents/<docId>` を取るまで存在しない
* `sitemap.xml` も `canonical` も無いので、そもそも 160 件の URL を
  クローラが見つける経路が「トップページの JS を実行する」しか無い

という状態で、実質インデックスされていなかった。

ここでやること
--------------
* `/doc/<docId>` に規則ごとの title / description / canonical / OG /
  JSON-LD を差し込む
* トップに全 160 件への素の `<a>` を置く（`#prerender`。SPA 起動時に
  index.tsx が消す）。JS を実行しないクローラにも一覧が見えるようにする。

**本文は同梱しない。** 以前は `content/<docId>/index.html` から `<article>` を
切り出して初回 HTML に入れていたが、全文の配信をやめたので本文そのものが
存在しない（`docs/architecture.md` 9 章）。露出を再開しても本文が初回 HTML に
戻らないよう、仕組みごと外してある。
* `/search` `/ask` は noindex + robots.txt で Disallow。クエリ違いで
  URL が無限に増える種類のページなので、クロール予算を使わせない。
* `/robots.txt` と `/sitemap.xml` を配る。

やらないこと
------------
本文を出さない。検索エンジンに読ませるのは規則名・条見出し・目次・
メタデータまでで、条文の本文は原本 PDF へ送る。
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape

from .excerpt import _CLAUSE_AT_LINE_START

# 正本の URL。Cloud Run の *.run.app ではなく独自ドメインを canonical に
# する（両方に同じ中身が出るので、寄せ先を固定しないと評価が割れる）。
DEFAULT_ORIGIN = "https://jp.motorsports-regulations.org"

# 検索エンジンへの露出。
#
# False の間は robots.txt が全面 Disallow になり、すべての応答に
# `X-Robots-Tag: noindex, nofollow`（server/app.py）と、HTML には
# `<meta name="robots">` も付く。sitemap.xml は作れるまま残すが、
# robots.txt からは案内しない。
#
# 2026-09-10: JAF のサイトポリシー（https://jaf.or.jp/common/websitepolicy）に
# 「当ウェブサイトは、JAFが所有、運営、管理しておりその資料のコピー、複製、
# 再版、ダウンロード、配布などは認めておりません」との明示があることが
# 分かったため、オーナーの判断で False にした。経緯は
# docs/architecture.md の 7-e と 9 章。
#
# False の間はトップの一覧（#prerender）も止める。クローラに読ませるための
# 仕組みなので、読ませないなら入れる意味がない。
#
# 再開するときはここを True に戻して出すだけでよい。ただし robots.txt で
# クロールを止めている間は、クローラは noindex を読めないので、すでに
# インデックスされた URL は URL だけ残ることがある。取り除きたいときは
# Search Console の削除ツールを使うか、いったんクロールを許して noindex を
# 読ませる（この 2 つは同時にはできない）。
SEARCH_INDEXING = False

SITE_NAME = "JAF モータースポーツ諸規則ビューア（非公式）"

# SNS でリンクを展開したときの画像。**全規則で共通の 1 枚**にしている。
# Slack・LINE・X は og:title と og:description を画像の横にテキストで出すので、
# 規則名を画像にも焼き込むのは重複になる。そのために 160 枚を週次で
# 作り直す手間は釣り合わない（`pipeline/build_icons.py` の og_card）。
OG_IMAGE = "/og-card.png"
OG_IMAGE_SIZE = (1200, 630)
OG_IMAGE_ALT = "JAF モータースポーツ諸規則 横断検索（非公式）"
PUBLISHER = "一般社団法人日本自動車連盟（JAF）"

HOME_TITLE = "JAF モータースポーツ諸規則を全文検索｜非公式ビューア"
HOME_DESCRIPTION = (
    "JAF が PDF で公開している国内モータースポーツ諸規則を横断検索できる非公式の"
    "ビューアです。競技規則・車両規定・統轄規定などをまたいで該当条文を探し、その場から"
    "JAF の原本 PDF の該当ページへ移動できます。条文の本文は掲載していません。"
    "JAF の公式サイトではありません。"
)

# SERP に出る長さの目安。日本語は 120 字前後で切られるので、それより
# 少し長めに作って末尾は削る。
DESCRIPTION_MAX = 150

_TITLE_TAG = re.compile(r"<title>.*?</title>", re.S)
_DESC_TAG = re.compile(r'<meta\s+name="description"[^>]*>', re.I)
_ROOT_DIV = re.compile(r'<div\s+id="root"\s*>\s*</div>')
_WS = re.compile(r"\s+")
_TRAILING_DATE = re.compile(r"[_\-]\d{8}$")
# 条見出しらしさの判定。excerpt 側と同じ基準（目印の直後に空白を要求する）を
# 使う。要求しないと、折り返した本文の行を見出しと誤認する。実測で
# 「第260条の特別規定に定められる数値とすることができる。(ただし、…」が
# description に入っていた。
_CLAUSE_HEAD = _CLAUSE_AT_LINE_START
# 見出しとして扱う長さの上限と、見出しには現れない文字
CLAUSE_HEAD_MAX = 30
_NOT_A_HEADING = ("。", "、")


def _norm(text: str) -> str:
    """全角英数と全角空白をならす.

    規則の見出しは「２０２7年日本カート選手権規定」のように全角と半角が
    混ざっている。利用者は半角で検索するので、title と description は
    NFKC をかけてから出す（本文は原文のまま残す）。
    """
    return _WS.sub(" ", unicodedata.normalize("NFKC", text or "")).strip()


def _display_title(title: str) -> str:
    """title タグ用。末尾の `_20260101` は検索語にならないので落とす。"""
    return _TRAILING_DATE.sub("", _norm(title))


def _meta(name: str, content: str) -> str:
    return f'<meta name="{name}" content="{html.escape(content, quote=True)}">'


def _prop(prop: str, content: str) -> str:
    return f'<meta property="{prop}" content="{html.escape(content, quote=True)}">'


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("／、。 ") + "…"


class Seo:
    def __init__(
        self,
        db_path: Path,
        content_dir: Path,
        dist_dir: Path,
        origin: str = DEFAULT_ORIGIN,
    ) -> None:
        self.db_path = Path(db_path)
        self.content_dir = Path(content_dir)
        self.dist_dir = Path(dist_dir)
        self.origin = origin.rstrip("/")
        self._shell_cache: tuple[float, str] | None = None
        self._sitemap_cache: tuple[float, str] | None = None

    # -- 下ごしらえ ---------------------------------------------------------

    def url(self, path: str) -> str:
        return f"{self.origin}{path}"

    def doc_path(self, doc_id: str) -> str:
        return f"/doc/{quote(doc_id, safe='')}"

    def _shell(self) -> str | None:
        """ビルド済み SPA の index.html（mtime でキャッシュ）."""
        index = self.dist_dir / "index.html"
        if not index.exists():
            return None
        mtime = index.stat().st_mtime
        if self._shell_cache is None or self._shell_cache[0] != mtime:
            self._shell_cache = (mtime, index.read_text(encoding="utf-8"))
        return self._shell_cache[1]

    def _connect(self) -> sqlite3.Connection | None:
        if not self.db_path.exists():
            return None
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    # -- 組み立て -----------------------------------------------------------

    def _compose(self, tags: list[str], prerender: str = "") -> str | None:
        shell = self._shell()
        if shell is None:
            return None
        # 既定の title / description は残すと二重になるので抜く
        out = _TITLE_TAG.sub("", shell, count=1)
        out = _DESC_TAG.sub("", out, count=1)
        out = out.replace("</head>", "".join(tags) + "</head>", 1)
        if prerender:
            replaced, n = _ROOT_DIV.subn(
                lambda m: m.group(0) + prerender, out, count=1
            )
            out = replaced if n else out.replace("</body>", prerender + "</body>", 1)
        return out

    def _common(
        self,
        *,
        title: str,
        description: str,
        path: str,
        kind: str,
        og_title: str | None = None,
    ) -> list[str]:
        url = self.url(path)
        return [
            f"<title>{html.escape(title)}</title>",
            *([] if SEARCH_INDEXING else [_meta("robots", "noindex,nofollow")]),
            _meta("description", description),
            f'<link rel="canonical" href="{html.escape(url, quote=True)}">',
            _prop("og:type", kind),
            _prop("og:site_name", SITE_NAME),
            _prop("og:locale", "ja_JP"),
            # SNS のカードは媒体名を別に出すので、og:title に重ねない
            _prop("og:title", og_title or title),
            _prop("og:description", description),
            _prop("og:url", url),
            _prop("og:image", self.url(OG_IMAGE)),
            _prop("og:image:width", str(OG_IMAGE_SIZE[0])),
            _prop("og:image:height", str(OG_IMAGE_SIZE[1])),
            _prop("og:image:alt", OG_IMAGE_ALT),
            # 画像を持つので大きいカードにする
            _meta("twitter:card", "summary_large_image"),
        ]

    def _jsonld(self, payload: dict) -> str:
        body = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
        return f'<script type="application/ld+json">{body}</script>'

    # -- ルートごとの中身 ---------------------------------------------------

    def page(self, path: str) -> str | None:
        """SPA のパスに対する HTML。扱わないパスは None（素の shell）."""
        if not path.startswith("/"):
            path = "/" + path
        segments = [s for s in path.split("/") if s]

        if not segments:
            return self._home()
        if segments[0] in ("search", "ask"):
            return self._noindex()
        if segments[0] == "doc" and len(segments) == 2:
            return self._document(segments[1])
        if segments[0] == "diff" and len(segments) == 3:
            return self._diff(segments[1], segments[2])
        return None

    def _home(self) -> str | None:
        tags = self._common(
            title=HOME_TITLE, description=HOME_DESCRIPTION, path="/", kind="website"
        )
        tags.append(
            self._jsonld(
                {
                    "@context": "https://schema.org",
                    "@type": "WebSite",
                    "name": SITE_NAME,
                    "url": self.url("/"),
                    "description": HOME_DESCRIPTION,
                    "inLanguage": "ja",
                    "isAccessibleForFree": True,
                    "potentialAction": {
                        "@type": "SearchAction",
                        "target": {
                            "@type": "EntryPoint",
                            "urlTemplate": self.url("/search?q={search_term_string}"),
                        },
                        "query-input": "required name=search_term_string",
                    },
                }
            )
        )
        return self._compose(tags, self._home_index() if SEARCH_INDEXING else "")

    def _home_index(self) -> str:
        """トップに、全 160 件への素の <a> を置く.

        規則一覧は JS で `/api/documents` を取ってから描いているので、
        JS を実行しないクローラにはトップから 1 本もリンクが見えない。
        sitemap.xml で URL は伝わるが、内部リンクが無いページは
        「どこからも参照されていない」扱いになる。ここに一覧を置いて
        束ねる（SPA 起動時に index.tsx が消す）。
        """
        con = self._connect()
        if con is None:
            return ""
        try:
            rows = con.execute(
                "SELECT doc_id, title, section, grp FROM docs"
                " ORDER BY source, section, grp, title"
            ).fetchall()
        finally:
            con.close()
        if not rows:
            return ""

        out = ['<div id="prerender"><h1>', html.escape(HOME_TITLE), "</h1><p>"]
        out.append(html.escape(HOME_DESCRIPTION))
        out.append("</p>")
        current: tuple[str, str] | None = None
        open_list = False
        for row in rows:
            key = (_norm(row["section"]), _norm(row["grp"]))
            if key != current:
                if open_list:
                    out.append("</ul>")
                label = "／".join(x for x in key if x) or "その他"
                out.append(f"<h2>{html.escape(label)}</h2><ul>")
                current, open_list = key, True
            out.append(
                f'<li><a href="{html.escape(self.doc_path(row["doc_id"]), quote=True)}">'
                f"{html.escape(_display_title(row['title']))}</a></li>"
            )
        if open_list:
            out.append("</ul>")
        out.append("</div>")
        return "".join(out)

    def _noindex(self, title: str = SITE_NAME) -> str | None:
        # title を消したままにすると、ブラウザのタブや履歴が空になる。
        # noindex なので SERP には出ないが、無題の HTML は返さない。
        return self._compose(
            [f"<title>{html.escape(title)}</title>", _meta("robots", "noindex,follow")]
        )

    def _document(self, doc_id: str) -> str | None:
        con = self._connect()
        if con is None:
            return None
        try:
            row = con.execute(
                "SELECT doc_id, title, source, section, grp, upload_date, pdf_url,"
                " page_count, series, edition FROM docs WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
            if row is None:
                return self._noindex()
            description = self._doc_description(con, row)
        finally:
            con.close()

        title = _display_title(row["title"])
        path = self.doc_path(doc_id)
        tags = self._common(
            title=f"{title}｜{SITE_NAME}",
            description=description,
            path=path,
            kind="article",
            og_title=title,
        )

        crumbs = [("ホーム", "/")]
        if row["section"]:
            crumbs.append((_norm(row["section"]), None))
        if row["grp"]:
            crumbs.append((_norm(row["grp"]), None))
        crumbs.append((title, path))

        page: dict = {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "name": title,
            "description": description,
            "url": self.url(path),
            "inLanguage": "ja",
            "isAccessibleForFree": True,
            "isPartOf": {"@type": "WebSite", "name": SITE_NAME, "url": self.url("/")},
            "breadcrumb": {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": i + 1,
                        "name": name,
                        **({"item": self.url(p)} if p else {}),
                    }
                    for i, (name, p) in enumerate(crumbs)
                ],
            },
        }
        if row["upload_date"]:
            page["dateModified"] = row["upload_date"]
        # 一次情報は JAF の PDF であることを構造化データでも示す
        citation: dict = {
            "@type": "CreativeWork",
            "name": _display_title(row["title"]),
            "publisher": {"@type": "Organization", "name": PUBLISHER},
        }
        if row["pdf_url"]:
            citation["url"] = row["pdf_url"]
        page["citation"] = citation
        tags.append(self._jsonld(page))

        return self._compose(tags)

    def _doc_description(self, con: sqlite3.Connection, row: sqlite3.Row) -> str:
        """規則ごとに違う description を作る.

        既定文をそのまま使うと 160 件が全部同じ説明になるため、条見出しを
        並べる。「第12条 安全ベルト」のような語で検索されたときに、どの
        規則のどこに何があるかが SERP で分かる。
        """
        heads: list[str] = []
        seen: set[str] = set()
        for (heading,) in con.execute(
            "SELECT heading FROM chunks WHERE doc_id = ? ORDER BY id LIMIT 200",
            (row["doc_id"],),
        ):
            text = _norm(heading)
            if (
                not text
                or text in seen
                or len(text) > CLAUSE_HEAD_MAX
                or any(ch in text for ch in _NOT_A_HEADING)
                or not _CLAUSE_HEAD.match(text)
            ):
                continue
            seen.add(text)
            heads.append(text)
            if len(heads) >= 12:
                break

        where = "／".join(x for x in (_norm(row["section"]), _norm(row["grp"])) if x)
        lead = _display_title(row["title"])
        if where:
            lead += f"（{where}）"
        # 本文は載せていないので「全文」と書かない。出せるのは条の一覧まで。
        lead += "の条文一覧。"
        if heads:
            return _clip(lead + "／".join(heads), DESCRIPTION_MAX)
        pages = f"{row['page_count']}ページ。" if row["page_count"] else ""
        return _clip(
            lead + f"{pages}本文は JAF の原本 PDF でご確認ください。",
            DESCRIPTION_MAX,
        )

    def _diff(self, doc_id: str, base_doc_id: str) -> str | None:
        con = self._connect()
        if con is None:
            return None
        try:
            row = con.execute(
                "SELECT title, edition FROM docs WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            base = con.execute(
                "SELECT title, edition FROM docs WHERE doc_id = ?", (base_doc_id,)
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return self._noindex()

        title = _display_title(row["title"])
        base_title = _display_title(base["title"]) if base else "前の版"
        path = f"/diff/{quote(doc_id, safe='')}/{quote(base_doc_id, safe='')}"
        return self._compose(
            self._common(
                title=f"{title} の改正点（{base_title} との比較）｜{SITE_NAME}",
                description=_clip(
                    f"{title} と {base_title} を条単位で比較し、変更・追加・削除された"
                    "条文を並べています。JAF 公開 PDF の自動変換による非公式の比較です。",
                    DESCRIPTION_MAX,
                ),
                path=path,
                kind="article",
            )
        )

    # -- robots / sitemap ---------------------------------------------------

    def robots_txt(self) -> str:
        if not SEARCH_INDEXING:
            # 全面 Disallow のときに Sitemap 行を残すと言っていることが
            # 食い違うので、案内しない（/sitemap.xml 自体は配り続ける）。
            return "User-agent: *\nDisallow: /\n"
        return (
            "User-agent: *\n"
            "Allow: /\n"
            # クエリ違いで URL が無限に増えるページ。クロール予算を食わせない。
            "Disallow: /search\n"
            "Disallow: /ask\n"
            "Disallow: /api/\n"
            "\n"
            f"Sitemap: {self.url('/sitemap.xml')}\n"
        )

    def sitemap_xml(self) -> str:
        """docs テーブルから作る（mtime でキャッシュ）."""
        if not self.db_path.exists():
            return self._sitemap([(self.url("/"), None)])
        mtime = self.db_path.stat().st_mtime
        if self._sitemap_cache is not None and self._sitemap_cache[0] == mtime:
            return self._sitemap_cache[1]

        entries: list[tuple[str, str | None]] = []
        con = self._connect()
        assert con is not None
        try:
            newest = con.execute("SELECT max(upload_date) FROM docs").fetchone()[0]
            entries.append((self.url("/"), newest))
            for row in con.execute(
                "SELECT doc_id, upload_date, diffs FROM docs ORDER BY doc_id"
            ):
                entries.append((self.url(self.doc_path(row["doc_id"])), row["upload_date"]))
                try:
                    diffs = json.loads(row["diffs"] or "[]")
                except json.JSONDecodeError:
                    diffs = []
                for d in diffs:
                    base = d.get("baseDocId")
                    if not base:
                        continue
                    entries.append(
                        (
                            self.url(
                                f"/diff/{quote(row['doc_id'], safe='')}"
                                f"/{quote(base, safe='')}"
                            ),
                            row["upload_date"],
                        )
                    )
        finally:
            con.close()

        xml = self._sitemap(entries)
        self._sitemap_cache = (mtime, xml)
        return xml

    @staticmethod
    def _sitemap(entries: list[tuple[str, str | None]]) -> str:
        out = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        ]
        for loc, lastmod in entries:
            out.append("<url>")
            out.append(f"<loc>{xml_escape(loc)}</loc>")
            if lastmod:
                out.append(f"<lastmod>{xml_escape(lastmod)}</lastmod>")
            out.append("</url>")
        out.append("</urlset>")
        return "\n".join(out) + "\n"
