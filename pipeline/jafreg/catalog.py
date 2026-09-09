"""JAF 諸規則ページから PDF カタログを抽出する.

JAF の配布ページ (motorsports.jaf.or.jp/regulations/information/*) は
Sitecore ベースの安定した DOM を持つ:

    div.tab-menu
      ul._header > li.js-slideTab-header      … 「カテゴリ一覧」「最近のアップデート」
      div._content.js-slideTab-body           … [0] カテゴリ一覧 / [1] 最近のアップデート
        div#01 … div#05                       … 大分類（h2.heading-primary）
          div.togglegroup
            div.toggle-accordion
              div._header                     … 中分類（アコーディオン見出し）
              div._content
                ul.list-type-iconBlock
                  li > a[href$=".pdf"]
                       p._title  … "国内競技規則_20250101(PDF：535.3 KB)"
                       p._date   … "アップロード日：2024年12月9日"

「最近のアップデート」タブは全件をアップロード日の降順で並べたものなので、
更新検知のフィードとしてそのまま使える。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE = "https://motorsports.jaf.or.jp"

SOURCE_PAGES: dict[str, str] = {
    "internal": f"{BASE}/regulations/information/internal",
    "international": f"{BASE}/regulations/information/international",
}

_TITLE_RE = re.compile(r"^(?P<title>.*?)\s*[（(]PDF[：:]\s*(?P<size>[\d.]+\s*[KMG]?B)\s*[）)]\s*$")
_DATE_RE = re.compile(r"(?P<y>\d{4})年\s*(?P<m>\d{1,2})月\s*(?P<d>\d{1,2})日")


@dataclass
class CatalogEntry:
    """カタログ 1 件 = PDF 1 本."""

    doc_id: str
    source: str  # internal / international
    source_url: str
    section: str  # 大分類（h2）
    group: str  # 中分類（アコーディオン見出し）
    title: str
    pdf_url: str
    upload_date: str | None  # ISO 8601 (YYYY-MM-DD)
    size_text: str | None
    order: int
    # fetch/convert フェーズで埋まる
    sha256: str | None = None
    bytes: int | None = None
    page_count: int | None = None
    converted_at: str | None = None
    pipeline_version: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _parse_date(text: str) -> str | None:
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    return f"{int(m['y']):04d}-{int(m['m']):02d}-{int(m['d']):02d}"


def make_doc_id(pdf_url: str) -> str:
    """PDF の URL から安定した ID を作る.

    JAF の media パスは `/-/media/1/3375/.../3477/<filename>.pdf` の形で、
    末尾のフォルダ番号 + ファイル名は改訂されても維持されやすい。
    衝突回避のためパス全体のハッシュ 6 桁を付ける。
    """
    path = pdf_url.split("?")[0]
    stem = path.rsplit("/", 1)[-1]
    stem = re.sub(r"\.pdf$", "", stem, flags=re.I)
    stem = re.sub(r"[^0-9A-Za-z_\-]+", "-", stem).strip("-").lower()[:60]
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:6]
    return f"{stem}-{digest}"


def parse_listing(html: str, source: str, source_url: str) -> list[CatalogEntry]:
    """1 ページ分の HTML からカタログを組み立てる."""
    soup = BeautifulSoup(html, "lxml")
    entries: list[CatalogEntry] = []
    seen: set[str] = set()
    order = 0

    tab_menu = soup.select_one("div.tab-menu")
    scope = tab_menu if tab_menu else soup
    # 「カテゴリ一覧」パネル（1 つ目の js-slideTab-body）だけを見る。
    # 2 つ目は「最近のアップデート」で同じ PDF の再掲。
    bodies = scope.select("div._content.js-slideTab-body")
    category_panel = bodies[0] if bodies else scope

    for section_div in category_panel.select("div[id]"):
        h2 = section_div.select_one("h2")
        if h2 is None:
            continue
        section = _clean(h2.get_text())

        accordions = section_div.select("div.toggle-accordion")
        if not accordions:
            accordions = [section_div]

        for acc in accordions:
            head = acc.select_one("div._header")
            group = _clean(head.get_text()) if head else ""
            for a in acc.select('a[href$=".pdf"], a[href*=".pdf?"]'):
                href = a.get("href")
                if not href:
                    continue
                pdf_url = urljoin(BASE, href)
                if pdf_url in seen:
                    continue
                seen.add(pdf_url)

                title_el = a.select_one("p._title")
                raw_title = _clean(title_el.get_text()) if title_el else _clean(a.get_text())
                m = _TITLE_RE.match(raw_title)
                title = _clean(m["title"]) if m else raw_title
                size_text = _clean(m["size"]) if m else None

                date_el = a.select_one("p._date")
                upload_date = _parse_date(_clean(date_el.get_text())) if date_el else None

                entries.append(
                    CatalogEntry(
                        doc_id=make_doc_id(pdf_url),
                        source=source,
                        source_url=source_url,
                        section=section,
                        group=group,
                        title=title,
                        pdf_url=pdf_url,
                        upload_date=upload_date,
                        size_text=size_text,
                        order=order,
                    )
                )
                order += 1

    return entries


def parse_recent_updates(html: str) -> dict[str, str]:
    """「最近のアップデート」タブから {pdf_url: upload_date} を得る."""
    soup = BeautifulSoup(html, "lxml")
    bodies = soup.select("div.tab-menu div._content.js-slideTab-body")
    if len(bodies) < 2:
        return {}
    out: dict[str, str] = {}
    for a in bodies[1].select('a[href$=".pdf"], a[href*=".pdf?"]'):
        href = a.get("href")
        date_el = a.select_one("p._date")
        if not href or not date_el:
            continue
        d = _parse_date(_clean(date_el.get_text()))
        if d:
            out[urljoin(BASE, href)] = d
    return out


def merge_catalogs(groups: Iterable[list[CatalogEntry]]) -> list[CatalogEntry]:
    merged: dict[str, CatalogEntry] = {}
    for entries in groups:
        for e in entries:
            merged.setdefault(e.doc_id, e)
    return list(merged.values())
