"""JAF の公示（お知らせ）一覧の取得。

<https://motorsports.jaf.or.jp/regulations/announcement>

## なぜ公示も見るのか

1. **更新を約 1 か月早く知れる。** 日本レース選手権規定 2027 年版は
   公示が 2026-08-04、諸規則一覧のアップロード日が 2026-09-09 でした。
2. **改正には「対比表」PDF が付く。** JAF 自身が新旧を並べた権威ある
   差分です。こちらの自動差分の裏取りに使えます。

対比表が付くかは公示ごとに違います。実データ（2024-01 以降 439 件）では
規則変更 107 件のうち **17 件に対比表**がありました。「一部改正」に多い
一方、「制定」でも付くことがあります（2027 年 日本ドリフト選手権規定の
制定など）。逆に日本レース選手権規定 2027 年版の制定公示は本体 PDF だけで、
変更点の列挙はありませんでした。つまり **対比表の有無で分岐する**のが
正しく、公示の種別では判断できません。

## 一覧は DOM ではなく JSON API から取る

一覧ページは JS で描画されるため HTML を読んでも項目が入っていません
（実測）。裏側の API を直接叩きます。

    /api/announcements/getlist?db=0&language=ja-JP&page=1&limit=50
      &searchItemID={EBE790C2-6FEB-421E-BF30-B9EE5C14C7C0}

`searchItemID` は必須です。省くと 500 が返ります（実測）。
DOM に依存しないので、サイトの見た目が変わっても壊れにくい構造です。
逆に API の形が変われば壊れるので、件数が取れなくなったら気づけるよう
呼び出し側でガードします。

詳細ページはサーバ描画なので HTML を読めます。ただしクラス名に依存すると
リニューアルで壊れるため、**公示 No. は正規表現、添付は `a[href$=.pdf]`**
という素朴な取り方にしてあります。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE = "https://motorsports.jaf.or.jp"
LIST_API = f"{BASE}/api/announcements/getlist"
SEARCH_ITEM_ID = "{EBE790C2-6FEB-421E-BF30-B9EE5C14C7C0}"
PAGE_SIZE = 50

# 「2026年9月10日」→ 2026-09-10
_JP_DATE = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
# 「公示No.2026-WEB064」
_NOTICE_NO = re.compile(r"公示\s*No\.?\s*([0-9A-Za-z‐-―\-]+)")
# 添付が新旧対照なら差分の正解として使える
_COMPARISON = re.compile(r"対比表|対照表|新旧")


def parse_jp_date(text: str) -> str | None:
    m = _JP_DATE.search(text or "")
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


@dataclass
class Attachment:
    text: str
    url: str
    comparison: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "url": self.url, "comparison": self.comparison}


@dataclass
class Announcement:
    id: str
    title: str
    date: str | None
    url: str
    category: str
    competitions: list[str] = field(default_factory=list)
    classifications: list[str] = field(default_factory=list)
    notice_no: str | None = None
    attachments: list[Attachment] = field(default_factory=list)

    @property
    def is_rule_change(self) -> bool:
        return "規則変更" in self.classifications

    @property
    def has_comparison(self) -> bool:
        return any(a.comparison for a in self.attachments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "date": self.date,
            "url": self.url,
            "category": self.category,
            "competitions": self.competitions,
            "classifications": self.classifications,
            "noticeNo": self.notice_no,
            "ruleChange": self.is_rule_change,
            "attachments": [a.to_dict() for a in self.attachments],
        }


def list_api_url(page: int, limit: int = PAGE_SIZE) -> str:
    from urllib.parse import urlencode

    return f"{LIST_API}?" + urlencode(
        {
            "db": 0,
            "language": "ja-JP",
            "page": page,
            "limit": limit,
            "searchItemID": SEARCH_ITEM_ID,
        }
    )


def parse_list_page(payload: str | dict[str, Any]) -> tuple[list[Announcement], int]:
    """API のレスポンスを Announcement に変換する。戻り値は (項目, 総件数)."""
    data = json.loads(payload) if isinstance(payload, str) else payload
    items = data.get("noticeAnnouncementsList") or []
    out: list[Announcement] = []
    for raw in items:
        url = raw.get("url") or ""
        out.append(
            Announcement(
                id=raw.get("id") or url,
                title=(raw.get("title") or "").strip(),
                date=parse_jp_date(raw.get("releaseDate") or ""),
                url=urljoin(BASE, url),
                category=(raw.get("category") or "").strip(),
                competitions=_split(raw.get("competition")),
                classifications=_split(raw.get("classification")),
            )
        )
    return out, int(data.get("totalCount") or 0)


def parse_detail(html: str, page_url: str) -> tuple[str | None, list[Attachment]]:
    """詳細ページから公示 No. と添付 PDF を取る。

    クラス名には依存しない。公示 No. は本文の正規表現、添付は拡張子で拾う。
    """
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup

    m = _NOTICE_NO.search(main.get_text(" ", strip=True))
    notice_no = m.group(1) if m else None

    attachments: list[Attachment] = []
    seen: set[str] = set()
    for a in main.find_all("a", href=True):
        href = a["href"]
        if ".pdf" not in href.lower():
            continue
        url = urljoin(page_url, href)
        if url in seen:
            continue
        seen.add(url)
        text = a.get_text(" ", strip=True)
        attachments.append(
            Attachment(text=text, url=url, comparison=bool(_COMPARISON.search(text)))
        )
    return notice_no, attachments


# 規則名の突き合わせ用。年度・施行日・記号の揺れを落とす。
_STRIP = re.compile(
    r"(?:19|20)\d{2}年度?|(?:19|20)\d{6}|第?\d+編|[\s　_・／/（）\(\)【】「」、,\.：:～~\-－—]"
)


def normalize_title(text: str) -> str:
    """規則名を突き合わせられる形に畳む。

    我々の文書タイトル（`自動車競技の組織に関する規定_20250401`）と、公示や
    添付の文言（`自動車競技の組織に関する規定_一部改正（新旧対照表）`）を
    同じ土俵に載せる。年度・施行日・記号を落とし、NFKC で全角を畳む。
    """
    return _STRIP.sub("", unicodedata.normalize("NFKC", text or ""))


# これより短い一致は偶然当たりやすいので採らない
MIN_TITLE_MATCH = 6


def match_titles(text: str, titles: dict[str, str]) -> str | None:
    """文言の中に規則名が含まれていれば、その鍵を返す。

    `titles` は {正規化済みの規則名: 鍵}。**最長一致**を採る。
    「自動車競技に関する申請・登録等手数料規定」と「カート競技に関する
    申請・登録等手数料規定」のように似た名前が並ぶため、短い側に
    引き寄せられると取り違える。
    """
    hay = normalize_title(text)
    best: tuple[int, str] | None = None
    for name, key in titles.items():
        if len(name) < MIN_TITLE_MATCH or name not in hay:
            continue
        if best is None or len(name) > best[0]:
            best = (len(name), key)
    return best[1] if best else None


def merge(
    previous: Iterable[dict[str, Any]], fresh: Iterable[Announcement]
) -> list[dict[str, Any]]:
    """既知の公示（詳細まで取得済み）と新しい一覧を混ぜる。

    詳細ページは 1 件ごとにリクエストが必要なので、既に取れているものは
    使い回す。日付の新しい順に並べて返す。
    """
    by_id: dict[str, dict[str, Any]] = {p["id"]: p for p in previous if p.get("id")}
    for ann in fresh:
        old = by_id.get(ann.id)
        new = ann.to_dict()
        if old:
            # 一覧側の情報は上書きするが、詳細で得たものは残す
            new["noticeNo"] = new["noticeNo"] or old.get("noticeNo")
            if not new["attachments"]:
                new["attachments"] = old.get("attachments") or []
        by_id[ann.id] = new
    return sorted(
        by_id.values(), key=lambda a: (a.get("date") or "", a.get("id") or ""), reverse=True
    )
