"""同じ規則の「年度版」をまとめる系列キー。

JAF は年度が変わると別ファイルとして公開するため、こちらでは別文書として
入ってきます。実際に 2026 年版と 2027 年版が同時に一覧に載っている規則が
あり（2026-09-09 時点で 5 件）、関係を示さないとどちらが有効か分かりません。

系列キーは **PDF のファイル名スラッグ**から取ります。タイトルで寄せると
FIA の「2026年競技規則_日本語版」が WEC・WRC・WTCR・地域ラリーで
4 本とも同名なので誤って束ねてしまいます（実測）。ファイル名側は
`fia_wec_sport_reg_ja` / `fia_wrc_sport_reg_ja` と分かれています。

    2026_jaf_sport_reg_race_20260101-801c15   → jaf_sport_reg_race
    jaf_sport_reg_race_20270101-01-9289b7     → jaf_sport_reg_race

docId の作り方が途中で変わっている（年度の接頭辞が付くものと付かない
もの、連番の有無）ため、落とす要素を並べて全部剥がします。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_HASH_SUFFIX = re.compile(r"-[0-9a-f]{6}$")
_SEQ_SUFFIX = re.compile(r"-\d{2}$")
_DATE_SUFFIX = re.compile(r"_(?:19|20)\d{6}$")
_YEAR_PREFIX = re.compile(r"^(?:19|20)\d{2}_")

# 「2027年日本レース選手権規定_20270101」→ 2027
_TITLE_YEAR = re.compile(r"(?:19|20)\d{2}")
# 施行日らしい 8 桁（docId 末尾・タイトル末尾のどちらにも出る）
_EFFECTIVE = re.compile(r"((?:19|20)\d{2})(\d{2})(\d{2})")


def series_key(doc_id: str) -> str:
    """年度・施行日・連番・ハッシュを落とした系列キー."""
    s = _HASH_SUFFIX.sub("", doc_id)
    s = _SEQ_SUFFIX.sub("", s)
    s = _DATE_SUFFIX.sub("", s)
    s = _YEAR_PREFIX.sub("", s)
    return s


def effective_date(doc_id: str, title: str = "") -> str | None:
    """施行日（YYYY-MM-DD）。docId 末尾の 8 桁から取り、無ければタイトルから."""
    for text in (doc_id, title):
        # 末尾から探す。docId は "..._20270101-01-9289b7" の形もある
        found = None
        for m in _EFFECTIVE.finditer(text):
            y, mo, d = m.groups()
            if "01" <= mo <= "12" and "01" <= d <= "31":
                found = m
        if found:
            y, mo, d = found.groups()
            return f"{y}-{mo}-{d}"
    return None


def edition_label(doc_id: str, title: str = "") -> str:
    """版の呼び名。「2027年版」が作れなければ施行日、それも無ければ空."""
    m = _TITLE_YEAR.search(title)
    if m and title.startswith(m.group(0)):
        return f"{m.group(0)}年版"
    eff = effective_date(doc_id, title)
    if eff:
        return f"{eff[:4]}年版"
    m = _YEAR_PREFIX.match(doc_id)
    if m:
        return f"{m.group(0)[:4]}年版"
    return ""


def sort_value(doc_id: str, title: str = "") -> str:
    """版を古い順に並べるための値。施行日があればそれを使う."""
    return effective_date(doc_id, title) or "0000-00-00"


def group_editions(docs: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """文書の並びを系列キーでまとめる。

    docs の各要素は少なくとも docId と title を持つこと。
    戻り値の各リストは施行日の古い順。
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for d in docs:
        doc_id = d.get("docId") or d.get("doc_id") or ""
        out.setdefault(series_key(doc_id), []).append(d)
    for members in out.values():
        members.sort(
            key=lambda d: (
                sort_value(d.get("docId") or d.get("doc_id") or "", d.get("title") or ""),
                d.get("docId") or d.get("doc_id") or "",
            )
        )
    return out
