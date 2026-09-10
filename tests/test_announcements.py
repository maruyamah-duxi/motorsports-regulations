"""公示一覧の解析の回帰テスト.

一覧は JSON API、詳細はサーバ描画の HTML。どちらも実際のレスポンスを
写したフィクスチャで確かめる。

    python tests/test_announcements.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from jafreg.announcements import (  # noqa: E402
    list_api_url,
    merge,
    parse_detail,
    parse_jp_date,
    parse_list_page,
)

# 実際の /api/announcements/getlist のレスポンスを写したもの（2026-09-10 取得）
LIST_JSON = json.dumps(
    {
        "noticeAnnouncementsList": [
            {
                "title": "各全日本選手権統一規則冊子の製作および配付終了について",
                "releaseDate": "2026年9月10日",
                "url": "/regulations/announcement/notice/2026/20260909_02",
                "target": "_self",
                "category": "公示",
                "categoryClass": "label-ms label-color-ms-public",
                "competition": "レース,ラリー,ジムカーナ,ダートトライアル,カート",
                "competitionClass": "label-ms,label-ms",
                "classification": "その他",
                "classificationClass": "label-ms label-color-ms-other2",
                "id": "fff54445dbd545178c42684066d56a80",
            },
            {
                "title": "自動車競技に関する申請・登録等手数料規定の改正について",
                "releaseDate": "2026年9月2日",
                "url": "/regulations/announcement/notice/2026/20260907_01",
                "target": "_self",
                "category": "公示",
                "categoryClass": "label-ms label-color-ms-public",
                "competition": "レース,カート",
                "competitionClass": "label-ms,label-ms",
                "classification": "規則変更,手続き",
                "classificationClass": "label-ms",
                "id": "aaa00000000000000000000000000001",
            },
            {
                "title": "モータースポーツ公示一覧（8月分）",
                "releaseDate": "2026年9月1日",
                "url": "/regulations/announcement/info/2026/20260901_01",
                "target": "_self",
                "category": "JAFからのお知らせ",
                "categoryClass": "label-ms",
                "competition": "",
                "classification": "",
                "id": "aaa00000000000000000000000000002",
            },
        ],
        "totalCount": 1187,
        "totalPages": 24,
    },
    ensure_ascii=False,
)

# 詳細ページ。クラス名に依存していないことを確かめたいので、
# 本物とは違う（架空の）クラス名を混ぜてある。
DETAIL_HTML = """
<html><body>
  <nav><a href="/regulations/announcement">一覧へ戻る</a></nav>
  <main>
    <h1>自動車競技に関する申請・登録等手数料規定の改正について</h1>
    <p class="whatever-class-name">公示No.2026-WEB077</p>
    <p>2026年9月2日</p>
    <p>添付のとおり改正いたしましたのでお知らせいたします。</p>
    <ul>
      <li><a href="/-/media/1/3375/4458/20260907_01.pdf">
        自動車競技に関する申請・登録等手数料規定対比表（PDF：88KB）</a></li>
      <li><a href="/-/media/1/3375/4458/20260907_01_02.pdf">
        カート競技に関する申請・登録等手数料規定対比表（PDF：87KB）</a></li>
      <li><a href="/-/media/1/3375/4458/20260907_01.pdf">重複したリンク（同じ URL）</a></li>
      <li><a href="/regulations/information">PDF ではないリンク</a></li>
    </ul>
  </main>
  <footer><a href="/-/media/footer/unrelated.pdf">フッタの無関係な PDF</a></footer>
</body></html>
"""


def test_list_api_url() -> None:
    url = list_api_url(3, limit=50)
    assert "page=3" in url and "limit=50" in url
    # searchItemID を省くと API が 500 を返す（実測）。必ず入っていること。
    assert "searchItemID=%7BEBE790C2-6FEB-421E-BF30-B9EE5C14C7C0%7D" in url


def test_parse_jp_date() -> None:
    assert parse_jp_date("2026年9月10日") == "2026-09-10"
    assert parse_jp_date("2026年12月1日") == "2026-12-01"
    assert parse_jp_date("日付なし") is None


def test_parse_list_page() -> None:
    items, total = parse_list_page(LIST_JSON)
    assert total == 1187
    assert len(items) == 3

    first = items[0]
    assert first.date == "2026-09-10"
    assert first.url.startswith("https://motorsports.jaf.or.jp/")
    assert first.competitions == ["レース", "ラリー", "ジムカーナ", "ダートトライアル", "カート"]
    assert first.classifications == ["その他"]
    assert not first.is_rule_change

    second = items[1]
    assert second.classifications == ["規則変更", "手続き"]
    assert second.is_rule_change, "複数分類でも規則変更を拾えること"

    third = items[2]
    assert third.category == "JAFからのお知らせ"
    assert third.competitions == [] and third.classifications == []


def test_parse_detail() -> None:
    notice_no, attachments = parse_detail(
        DETAIL_HTML, "https://motorsports.jaf.or.jp/regulations/announcement/notice/2026/20260907_01"
    )
    assert notice_no == "2026-WEB077"

    urls = [a.url for a in attachments]
    assert len(attachments) == 2, f"重複と非 PDF を除いて 2 件のはず: {urls}"
    assert all(u.startswith("https://motorsports.jaf.or.jp/-/media/") for u in urls)
    # main の外（フッタ）の PDF は拾わない
    assert not any("unrelated" in u for u in urls)
    # 対比表は差分の正解として使えるので印を付ける
    assert all(a.comparison for a in attachments)


def test_parse_detail_without_comparison() -> None:
    html = """<main><p>公示No.2026-WEB034</p>
      <a href="/x/20260420_02.pdf">自動車競技の組織に関する規定（PDF：219KB）</a></main>"""
    notice_no, attachments = parse_detail(html, "https://motorsports.jaf.or.jp/a/b")
    assert notice_no == "2026-WEB034"
    assert len(attachments) == 1
    assert not attachments[0].comparison, "本体 PDF だけなら対比表ではない"


def test_merge_keeps_detail() -> None:
    items, _ = parse_list_page(LIST_JSON)
    # 1 回目: 詳細まで取得済みの状態を作る
    target = items[1]
    target.notice_no, target.attachments = parse_detail(DETAIL_HTML, target.url)
    first_pass = merge([], items)
    assert len(first_pass) == 3

    # 2 回目: 一覧だけ取り直しても、詳細で得た情報が残ること
    again, _ = parse_list_page(LIST_JSON)
    second_pass = merge(first_pass, again)
    kept = next(m for m in second_pass if m["id"] == target.id)
    assert kept["noticeNo"] == "2026-WEB077"
    assert len(kept["attachments"]) == 2

    # 日付の新しい順
    dates = [m["date"] for m in second_pass]
    assert dates == sorted(dates, reverse=True)


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
