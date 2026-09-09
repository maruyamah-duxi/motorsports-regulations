"""catalog.parse_listing の回帰テスト.

JAF の実ページと同じ入れ子（タブ / アコーディオン / 再掲リスト / Cookie バナー）を
再現したフィクスチャで、拾うべきものだけを拾えているか確認する。

    python -m pytest tests/ -q      # pytest があれば
    python tests/test_catalog.py    # 無くても単体で走る
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from jafreg.catalog import parse_listing  # noqa: E402


def _pdf_li(name: str, title: str, size: str, date: str) -> str:
    return f"""
      <li><a href="/-/media/1/3375/3379/3400/3462/3463/3477/{name}.pdf" target="_blank">
        <img class="_img" src="/common/ms/images/icon/pdf.svg" alt="pdf">
        <div class="_text">
          <p class="_title">{title}(PDF：{size})</p>
          <p class="_date">アップロード日：{date}</p>
        </div>
      </a></li>"""


INTERNAL_HTML = f"""
<html><body>
<h1>国内モータースポーツ諸規則など</h1>
<div class="l-container"><div class="tab-menu js-slideTab">
  <ul class="_header">
    <li class="js-slideTab-header is-active">カテゴリ一覧</li>
    <li class="js-slideTab-header">最近のアップデート</li>
  </ul>

  <div class="_content js-slideTab-body is-active">
    <div id="01">
      <h2 class="heading-primary">2026年 JAF国内競技規則・細則・各種規定</h2>
      <div class="togglegroup">
        <div class="toggle-accordion js-slideToggle">
          <div class="_header js-slideToggle-header">国内競技規則</div>
          <div class="_content js-slideToggle-body">
            <ul class="list-type-iconBlock">
              {_pdf_li("2025_jaf_kokunai_sporting_code_20250101", "国内競技規則_20250101", "535.3 KB", "2024年12月9日")}
            </ul>
          </div>
        </div>
        <div class="toggle-accordion js-slideToggle">
          <div class="_header js-slideToggle-header">国内競技規則細則</div>
          <div class="_content js-slideToggle-body">
            <ul class="list-type-iconBlock">
              {_pdf_li("1386_rules1-2", "国内競技規則細則_20260101", "1.2 MB", "2026年9月9日")}
              {_pdf_li("jaf_kitei_soshiki_20270101-01", "自動車競技の組織に関する規定_20270101", "251.7 KB", "2026年8月1日")}
            </ul>
          </div>
        </div>
      </div>
    </div>
    <div id="02">
      <h2 class="heading-primary">2026年 JAF国内競技車両規則</h2>
      <div class="togglegroup">
        <div class="toggle-accordion js-slideToggle">
          <div class="_header js-slideToggle-header">第5編 細則</div>
          <div class="_content js-slideToggle-body">
            <ul class="list-type-iconBlock">
              {_pdf_li("jaf_05_saisoku_race_soubi_20260101", "レース競技に参加するドライバーの装備品に関する細則_20260101", "839.8 KB", "2026年9月9日")}
            </ul>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- 「最近のアップデート」タブ: 同じ PDF の再掲。ここは拾ってはいけない -->
  <div class="_content js-slideTab-body">
    <ul class="list-type-iconBlock">
      {_pdf_li("jaf_05_saisoku_race_soubi_20260101", "レース競技に参加するドライバーの装備品に関する細則_20260101", "839.8 KB", "2026年9月9日")}
      {_pdf_li("1386_rules1-2", "国内競技規則細則_20260101", "1.2 MB", "2026年9月9日")}
    </ul>
  </div>
</div></div>

<!-- Cookie 同意バナー: 見出しも PDF も拾ってはいけない -->
<div id="ot-pc-content">
  <h2>クッキー詳細設定</h2>
  <div class="toggle-accordion">
    <div class="_header">パフォーマンス Cookie</div>
    <ul><li><a href="/policy/cookie.pdf"><p class="_title">クッキーポリシー(PDF：10 KB)</p></a></li></ul>
  </div>
</div>
</body></html>
"""

# 国際ページはタブが無く h2 が直に並ぶ形
INTERNATIONAL_HTML = f"""
<html><body>
<h1>国際モータースポーツ諸規則など</h1>
<h2>FIA国際モータースポーツ競技規則</h2>
<div class="toggle-accordion">
  <div class="_header">FIA国際モータースポーツ競技規則付則_日本語版</div>
  <ul class="list-type-iconBlock">
    {_pdf_li("fia_appendix_l_2026", "FIA国際モータースポーツ競技規則付則L項_2026", "2.1 MB", "2026年3月1日")}
  </ul>
</div>
<h2>FIAガイドライン等</h2>
<div class="toggle-accordion">
  <div class="_header">FIAラリー安全ガイドライン_日本語版</div>
  <ul class="list-type-iconBlock">
    {_pdf_li("fia_rally_safety_2026", "FIAラリー安全ガイドライン_2026", "900 KB", "2026年2月10日")}
  </ul>
</div>
</body></html>
"""


def test_internal():
    e = parse_listing(INTERNAL_HTML, "internal", "https://example.invalid/internal")
    assert len(e) == 4, [x.title for x in e]

    first = e[0]
    assert first.section == "2026年 JAF国内競技規則・細則・各種規定"
    assert first.group == "国内競技規則"
    assert first.title == "国内競技規則_20250101"          # (PDF：…) は落とす
    assert first.size_text == "535.3 KB"
    assert first.upload_date == "2024-12-09"               # ISO 8601 に正規化
    assert first.pdf_url.startswith("https://motorsports.jaf.or.jp/-/media/")

    assert e[2].group == "国内競技規則細則"
    assert e[3].section == "2026年 JAF国内競技車両規則"
    assert e[3].group == "第5編 細則"

    # Cookie バナーの PDF と、再掲リストの重複を拾っていない
    assert not any("cookie" in x.pdf_url for x in e)
    assert len({x.pdf_url for x in e}) == len(e)
    assert not any("クッキー" in x.section for x in e)


def test_international():
    e = parse_listing(INTERNATIONAL_HTML, "international", "https://example.invalid/international")
    assert len(e) == 2, [x.title for x in e]
    assert e[0].section == "FIA国際モータースポーツ競技規則"
    assert e[1].section == "FIAガイドライン等"
    assert e[1].group == "FIAラリー安全ガイドライン_日本語版"


def test_doc_id_is_stable():
    a = parse_listing(INTERNAL_HTML, "internal", "u")
    b = parse_listing(INTERNAL_HTML, "internal", "u")
    assert [x.doc_id for x in a] == [x.doc_id for x in b]
    assert len({x.doc_id for x in a}) == len(a)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("すべて通過")
