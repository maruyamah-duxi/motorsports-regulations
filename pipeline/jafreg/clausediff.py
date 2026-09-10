"""条単位の改正差分。

JAF 自身が出していない機能なので、このサービスの一番の売りになり得ます。
やることは 3 段です。

1. `document.json` を**見出し単位の条項**に切る
2. 新旧の条項を**条項番号の階層**で突き合わせる
3. 対応した条項の本文を行単位で差分にする

## 鍵の作り方でつまずいた点

条項の鍵は「条項番号の連なり」にしますが、**レベル 1（文書タイトル）は
除きます**。タイトルには年度が入っており（「２０２6年日本レース選手権規定」
→ clause が `２０２6`）、鍵に含めると全条の鍵が変わって
**50 条すべてが「消えた／増えた」になります**（実測でそうなりました）。

また `第2条 > １`『１．全日本選手権』のような枝番は単独では重複するため、
階層を連ねて一意にします。全角数字は NFKC で畳みます。

## 突き合わせの方針

番号が一致する条項を対応付け、余ったものは見出しの文字列の近さで拾います。
条番号がずれる改正（条の挿入で以降が繰り下がる）を、全条の書き換えとして
出さないためです。
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# 見出しの近さでの救済をどこまで許すか。低くすると無関係な条が繋がる。
FUZZY_THRESHOLD = 0.72

# 年号（19xx / 20xx）。年度版の比較では「2026年 → 2027年」の読み替えだけが
# 大量に出るので、それだけの変更を見分けるために使う。
_YEAR = re.compile(r"(?:19|20)\d{2}")


def normalize(text: str | None) -> str:
    """全角英数・半角カナの揺れを畳む。差分の見た目を安定させる."""
    return unicodedata.normalize("NFKC", text or "")


def _clause_token(block: dict[str, Any]) -> str:
    return normalize(block.get("clause") or block.get("text") or "")


@dataclass
class Clause:
    key: str
    heading: str
    anchor: str | None
    page: int | None
    lines: list[str] = field(default_factory=list)

    @property
    def body(self) -> str:
        return "\n".join(self.lines).strip()


def split_clauses(doc: dict[str, Any]) -> list[Clause]:
    """document.json を見出し単位の条項に切る."""
    out: list[Clause] = []
    stack: list[tuple[int, str]] = []
    cur: Clause | None = None

    def start(block: dict[str, Any]) -> Clause:
        level = int(block.get("level") or 3)
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, _clause_token(block)))
        # レベル 1 はタイトル（年度が入る）なので鍵から外す
        key = " > ".join(tok for lv, tok in stack if lv >= 2)
        return Clause(
            key=key or "(前文)",
            heading=block.get("text") or "",
            anchor=block.get("id"),
            page=block.get("page"),
        )

    for block in doc.get("blocks", []):
        btype = block.get("type")
        if btype == "heading":
            if cur is not None:
                out.append(cur)
            cur = start(block)
            continue
        if cur is None:
            cur = Clause(key="(前文)", heading="", anchor=None, page=block.get("page"))
        if btype in ("paragraph", "caption"):
            cur.lines.append(normalize(block.get("text")))
        elif btype == "table":
            for row in block.get("rows") or []:
                cur.lines.append(" | ".join(normalize(c) for c in row))
    if cur is not None:
        out.append(cur)

    # 同じ鍵が複数出たら連番で分ける（同名の枝番が並ぶ規則がある）
    seen: dict[str, int] = {}
    for c in out:
        n = seen.get(c.key, 0)
        seen[c.key] = n + 1
        if n:
            c.key = f"{c.key}#{n + 1}"
    return out


def _match(
    old: list[Clause], new: list[Clause]
) -> tuple[list[tuple[Clause, Clause]], list[Clause], list[Clause]]:
    """条項番号で突き合わせ、余りを見出しの近さで拾う."""
    old_by_key = {c.key: c for c in old}
    new_by_key = {c.key: c for c in new}

    pairs = [(old_by_key[k], new_by_key[k]) for k in new_by_key if k in old_by_key]
    left = [c for c in old if c.key not in new_by_key]
    right = [c for c in new if c.key not in old_by_key]

    # 条番号がずれた改正を「全条書き換え」にしないための救済
    for nc in list(right):
        best, score = None, 0.0
        for oc in left:
            s = difflib.SequenceMatcher(None, normalize(oc.heading), normalize(nc.heading)).ratio()
            if s > score:
                best, score = oc, s
        if best is not None and score >= FUZZY_THRESHOLD:
            pairs.append((best, nc))
            left.remove(best)
            right.remove(nc)

    # 新しい版の並び順で出す
    order = {c.key: i for i, c in enumerate(new)}
    pairs.sort(key=lambda p: order.get(p[1].key, 0))
    return pairs, left, right


def _line_diff(a: str, b: str) -> list[dict[str, str]]:
    """行単位の差分。前後の文脈は付けない（条項自体が単位なので）."""
    out: list[dict[str, str]] = []
    for line in difflib.unified_diff(
        a.split("\n"), b.split("\n"), lineterm="", n=0
    ):
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("-"):
            out.append({"op": "del", "text": line[1:]})
        elif line.startswith("+"):
            out.append({"op": "add", "text": line[1:]})
    return out


def _year_only(a: str, b: str) -> bool:
    """違いが年号の表記だけかを判定する。

    **類似度で「軽微」を決めてはいけません。** 最初はそうしていましたが、
    走行距離が「30km → 20分」に変わった条が類似度 0.971 で「軽微」に
    分類されました。長い条文の中の 1 か所の数値変更は類似度が高く出るため、
    規則の実質的な変更を「軽微」と呼んでしまいます。

    そこで判定は「年号を伏せたら同一になるか」という**厳密な条件**にします。
    これなら年度版の読み替えだけを正確に切り分けられます。
    なお、このラベルが付いても差分の行は必ず表示します（隠さない）。
    """
    return _YEAR.sub("YYYY", a) == _YEAR.sub("YYYY", b)


def diff_documents(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """新旧の document.json を条単位で突き合わせる."""
    oc = split_clauses(old)
    nc = split_clauses(new)
    pairs, removed, added = _match(oc, nc)

    changes: list[dict[str, Any]] = []
    unchanged = 0
    for o, n in pairs:
        if o.body == n.body:
            unchanged += 1
            continue
        ratio = difflib.SequenceMatcher(None, o.body, n.body).ratio()
        changes.append(
            {
                "status": "year_only" if _year_only(o.body, n.body) else "changed",
                "key": n.key,
                "heading": n.heading,
                "anchor": n.anchor,
                "page": n.page,
                "previousPage": o.page,
                "similarity": round(ratio, 3),
                "lines": _line_diff(o.body, n.body),
            }
        )

    for c in added:
        changes.append(
            {
                "status": "added",
                "key": c.key,
                "heading": c.heading,
                "anchor": c.anchor,
                "page": c.page,
                "lines": [{"op": "add", "text": t} for t in c.body.split("\n") if t],
            }
        )
    for c in removed:
        changes.append(
            {
                "status": "removed",
                "key": c.key,
                "heading": c.heading,
                "anchor": None,
                "previousPage": c.page,
                "lines": [{"op": "del", "text": t} for t in c.body.split("\n") if t],
            }
        )

    # 表示順は新しい版の並び、削除された条は末尾
    order = {c.key: i for i, c in enumerate(nc)}
    changes.sort(key=lambda c: (c["status"] == "removed", order.get(c["key"], 10**6)))

    return {
        "base": {
            "docId": old.get("docId"),
            "title": old.get("title"),
            "uploadDate": old.get("uploadDate"),
        },
        "target": {
            "docId": new.get("docId"),
            "title": new.get("title"),
            "uploadDate": new.get("uploadDate"),
        },
        "summary": {
            "clauses": len(nc),
            "unchanged": unchanged,
            "changed": sum(1 for c in changes if c["status"] == "changed"),
            "yearOnly": sum(1 for c in changes if c["status"] == "year_only"),
            "added": sum(1 for c in changes if c["status"] == "added"),
            "removed": sum(1 for c in changes if c["status"] == "removed"),
        },
        "changes": changes,
    }
