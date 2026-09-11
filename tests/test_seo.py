"""検索エンジン向け HTML / sitemap の回帰テスト.

    python tests/test_seo.py

外部アクセスは無し。docs / chunks を持つ小さな SQLite と、変換済み
HTML を模した content/ を一時ディレクトリに作って確かめる。
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import seo as seo_module  # noqa: E402
from server.seo import Seo, _truncate_html  # noqa: E402

ORIGIN = "https://example.test"

SHELL = (
    '<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">'
    '<meta name="description" content="既定の説明">'
    "<title>既定のタイトル</title></head><body>"
    '<div id="root"></div></body></html>'
)

ARTICLE = (
    '<!doctype html><html><head><title>x</title></head><body>'
    '<article class="doc"><h1>2026年ラリー競技規則</h1>'
    '<p class="meta">国内競技規則 ／ 第3編</p>'
    '<h2 id="c1">第1条 総則</h2><p>本規則は…</p>'
    '<figure><img src="assets/fig-p0001-01.webp" alt="図版"></figure>'
    "</article></body></html>"
)


class indexing:
    """`SEARCH_INDEXING` を一時的に切り替える（露出のオン／オフ両方を試す）."""

    def __init__(self, value: bool) -> None:
        self.value = value

    def __enter__(self) -> None:
        self.saved = seo_module.SEARCH_INDEXING
        seo_module.SEARCH_INDEXING = self.value

    def __exit__(self, *exc: object) -> None:
        seo_module.SEARCH_INDEXING = self.saved


def _fixture(tmp: Path) -> Seo:
    content = tmp / "content"
    dist = tmp / "dist"
    (dist).mkdir()
    (dist / "index.html").write_text(SHELL, encoding="utf-8")

    for doc_id in ("2026_rally-aaa", "2025_rally-bbb"):
        d = content / doc_id
        d.mkdir(parents=True)
        (d / "index.html").write_text(ARTICLE, encoding="utf-8")

    db = tmp / "search.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE docs (doc_id TEXT PRIMARY KEY, title TEXT, source TEXT,
          section TEXT, grp TEXT, upload_date TEXT, pdf_url TEXT,
          page_count INTEGER, chars INTEGER, figures INTEGER, tables INTEGER,
          series TEXT, edition TEXT, history TEXT, diffs TEXT, announcements TEXT);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, doc_id TEXT, heading TEXT);
        """
    )
    con.execute(
        "INSERT INTO docs VALUES ('2026_rally-aaa','２０２6年ラリー競技規則_20260101',"
        "'JAF','国内競技規則','第3編','2026-04-01',"
        "'https://motorsports.jaf.or.jp/x.pdf',120,0,0,0,'rally','2026',"
        "'[]','[{\"baseDocId\":\"2025_rally-bbb\"}]','[]')"
    )
    con.execute(
        "INSERT INTO docs VALUES ('2025_rally-bbb','2025年ラリー競技規則_20250101',"
        "'JAF','国内競技規則','第3編','2025-04-01',NULL,110,0,0,0,'rally','2025',"
        "'[]','[]','[]')"
    )
    con.executemany(
        "INSERT INTO chunks (doc_id, heading) VALUES ('2026_rally-aaa', ?)",
        [("２０２6年ラリー競技規則",), ("第１条　総　則",), ("第２条　安全ベルト",), ("１．適用",)],
    )
    con.commit()
    con.close()
    return Seo(db, content, dist, ORIGIN)


def test_document_head() -> None:
    with indexing(True):
        with tempfile.TemporaryDirectory() as tmp:
            s = _fixture(Path(tmp))
            page = s.page("/doc/2026_rally-aaa")
            assert page is not None

            # 既定の title / description は残さない（二重になる）
            assert "既定のタイトル" not in page
            assert "既定の説明" not in page
            # 全角は NFKC でならし、末尾の _20260101 は落とす
            assert "<title>2026年ラリー競技規則｜" in page
            assert "_20260101" not in page.split("</head>")[0]
            assert (
                '<link rel="canonical" href="https://example.test/doc/2026_rally-aaa">'
                in page
            )
            # description は条見出しを並べて、規則ごとに違うものにする
            assert "第1条 総則" in page and "第2条 安全ベルト" in page
            # 条見出しでないものは入れない
            assert "1.適用" not in page
            assert '"@type": "BreadcrumbList"' in page
            assert '"dateModified": "2026-04-01"' in page
            # 一次情報が JAF であることを構造化データでも示す
            assert "日本自動車連盟" in page


def test_document_prerender() -> None:
    with indexing(True):
        with tempfile.TemporaryDirectory() as tmp:
            s = _fixture(Path(tmp))
            page = s.page("/doc/2026_rally-aaa")
            assert page is not None
            # 本文が #root の後ろに入る（React は #root しか触らない）
            root = page.index('<div id="root"></div>')
            pre = page.index('id="prerender"')
            assert root < pre
            assert "第1条 総則" in page or "第1条 総則" in page
            assert "<p>本規則は…</p>" in page
            # 図版の相対パスは絶対パスに直す（/doc/assets/… を見に行かせない）
            assert 'src="/content/2026_rally-aaa/assets/fig-p0001-01.webp"' in page
            assert 'src="assets/' not in page


def test_routes() -> None:
    with indexing(True):
        with tempfile.TemporaryDirectory() as tmp:
            s = _fixture(Path(tmp))

            home = s.page("/")
            assert home is not None
            # トップに全件への素のリンクを置く（JS 無しでも辿れるように）
            assert home.count('<a href="/doc/') == 2

            # クエリで無限に増えるページはインデックスさせない
            for path in ("/search", "/ask"):
                page = s.page(path)
                assert page is not None
                assert '<meta name="robots" content="noindex,follow">' in page
                assert "<title>" in page  # 無題の HTML は返さない

            diff = s.page("/diff/2026_rally-aaa/2025_rally-bbb")
            assert diff is not None
            assert "2025年ラリー競技規則" in diff
            assert "/diff/2026_rally-aaa/2025_rally-bbb" in diff

            # 存在しない docId は noindex（素の shell を返して 200 にしない）
            assert '"noindex,follow"' in (s.page("/doc/nope") or "")
            # 扱わないパスは None（従来どおり素の index.html）
            assert s.page("/whatever") is None


def test_sitemap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        s = _fixture(Path(tmp))
        xml = s.sitemap_xml()
        assert xml.startswith("<?xml")
        # トップ + 規則 2 件 + 差分 1 件
        assert xml.count("<loc>") == 4
        assert "<loc>https://example.test/doc/2026_rally-aaa</loc>" in xml
        assert "<loc>https://example.test/diff/2026_rally-aaa/2025_rally-bbb</loc>" in xml
        assert "<lastmod>2026-04-01</lastmod>" in xml


def test_indexing_switch() -> None:
    """`SEARCH_INDEXING` の両方の状態で、robots と head が食い違わないこと."""
    with tempfile.TemporaryDirectory() as tmp:
        s = _fixture(Path(tmp))

        with indexing(True):
            robots = s.robots_txt()
            page = s.page("/doc/2026_rally-aaa") or ""
            assert "Sitemap: https://example.test/sitemap.xml" in robots
            assert "Disallow: /search" in robots
            assert "Disallow: /\n" not in robots  # 全面禁止にはしない
            assert 'name="robots"' not in page

        with indexing(False):
            robots = s.robots_txt()
            page = s.page("/doc/2026_rally-aaa") or ""
            assert robots == "User-agent: *\nDisallow: /\n"
            # 全面 Disallow のときに Sitemap 行を残すと言っていることが食い違う
            assert "Sitemap:" not in robots
            assert '<meta name="robots" content="noindex,nofollow">' in page
            assert s.page("/") is not None  # トップも同じ扱い
            assert 'name="robots"' in (s.page("/") or "")
            # クローラに読ませないならプリレンダを入れる意味がない。
            # 入れたままだと同じ本文を #prerender と React で二重に配る。
            assert 'id="prerender"' not in page
            assert 'id="prerender"' not in (s.page("/") or "")
            # head の作りは変えない（再開時にそのまま出せるように）
            assert '<link rel="canonical"' in page
            assert s.sitemap_xml().count("<loc>") == 4


def test_truncate_closes_tags() -> None:
    # 目次そのものが上限より長いときに、直前の </p> まで戻ってしまう
    # 事故があった。タグを数えて切り、閉じ忘れを補う。
    body = '<article class="doc"><p>あ</p><nav><ol>' + (
        "".join(f"<li><a href=\"#c{i}\">第{i}条</a></li>" for i in range(4000))
    ) + "</ol></nav><p>い</p></article>"
    out, truncated = _truncate_html(body, 20_000)
    assert truncated
    assert len(out.encode("utf-8")) < 21_000
    # 目次の途中で切れていて、かつタグは閉じている
    assert out.endswith("</li></ol></nav></article>") or out.endswith(
        "</ol></nav></article>"
    )
    assert out.count("<li>") == out.count("</li>")
    # 上限以下なら手を付けない
    same, flag = _truncate_html("<p>短い</p>", 20_000)
    assert same == "<p>短い</p>" and flag is False


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通過")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
