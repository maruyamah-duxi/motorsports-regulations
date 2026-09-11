"""条文中の「他の規則を見よ」という参照をたどる.

なぜ必要か
----------
JAF の諸規則は互いを指し合っている。第2編ラリー車両規定 5.2 はこう書くだけで、
材質も寸法も取り付け方法も**別の規則にしか載っていない**。

    RJ車両は、JAF国内競技車両規則第1編レース車両規定第4章公認車両および
    登録車両に関する安全規定に従ったロールケージを装着し…

追わないと、質問「RJ車両のロールバーはボルトオンでもよいか」に対して検索は
第2編 5.4.2（材質と 40mm×2mm）と 5.4.4（ボルト/溶接）を拾う。**どちらも
RPN・RF・AE車両の規定**で、RJ車両には適用されない。支配している第1編第4章
第6条は「RJ」も「ボルト」も含まないので拾われない。その状態で要約させると、
手近な別区分の数値を RJ の答えとして出す。条を取り違えた要約は黙っているより悪い。

どう解決するか
--------------
**条番号で直接当てるのは諦めた。** 最初はそうしたが、第1編の「第6条」は
電装品（P.48）・サスペンション（P.83）・ロールケージ（P.21）と複数あり、
正解の heading_path に第4章が入っていないため区別できなかった（見出しの
検出が効いていない領域。全体の 19% が 10 ページ以上をまたぐ問題と同根）。

代わりに **参照先の規則に絞り、条番号を見出しの前置きとして使う**。
第1編の規則は第6条の中身を 6.1 / 6.2.x / 6.3.x と振るので、
「見出しが 6. で始まるもの」に絞れば第6条の中に入る。実測で、質問の語で
並べ替えた上位 6 件すべてがロールケージの条文になった。

付則J項のように項番号が条ごとに振り直される規則（第253条の中が 8.2.4）では
前置きが効かない。そのときは**何も足さない**。規則名だけに絞って引く
フォールバックを最初は入れたが、付則J項で「第253条4に合致しなければ
ならない。マスターシリンダー」という**折り返した本文を見出しと誤検出した
チャンク**に当たり、根拠の枠を 3 つ潰した（実測）。当てられないときに
当てずっぽうで足すより、足さないほうがよい。
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass

# 「第6条」「第253条第8項」
_ARTICLE = re.compile(r"第\s*([0-9０-９]+)\s*条(?:\s*第?\s*([0-9０-９]+)\s*項)?")
# 「6.3.2.1.4)」
_DOTTED = re.compile(r"(?<![0-9.])([0-9]+(?:\.[0-9]+){1,4})\s*\)?")
# タイトルから参照の鍵を作るときに落とす接尾辞
_TITLE_TAIL = re.compile(r"(?:_日本語版|_仏語英語版|_英語)?(?:[_\-][0-9]{8})?(?:-[0-9a-f]{6})?$")

# 規則名は条項の何文字前までに現れていれば「その規則の話」とみなすか。
# 離れすぎた組み合わせを拾うと、無関係な規則を根拠に混ぜてしまう。
NEAR_WINDOW = 60
# 鍵が短いと誤って当たる（「規定」だけ等）
MIN_TITLE_KEY = 8
# 見出しらしさ。変換後の本文には折り返した行が見出しとして混ざっている
# （「第253条4に合致しなければならない。マスターシリンダー」など）。
# 参照先としてそれを選ぶと根拠が汚れるので、長さと句点で弾く。
HEADING_MAX = 40
_NOT_A_HEADING = ("。", "、")
# 1 つの参照から足すチャンク数と、1 回の回答で足す上限
PER_REFERENCE = 3
MAX_FOLLOWED = 5


def _squash(text: str) -> str:
    """空白を落として比較する（見出しは「第 6 条　ロールケージ」の形）."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


@dataclass(frozen=True)
class Reference:
    """「どの規則の、どのあたりか」."""

    doc_key: str
    #: 見出しの前置きに使う番号。「第6条」なら "6"、「6.3.2」ならそのまま
    prefix: str
    #: 画面やログに出す用
    label: str


def title_keys(con: sqlite3.Connection) -> dict[str, list[str]]:
    """参照に使える規則名 → docId。`docs` のタイトルから作る（固定表は持たない）."""
    keys: dict[str, list[str]] = {}
    for doc_id, title in con.execute("SELECT doc_id, title FROM docs"):
        key = _squash(_TITLE_TAIL.sub("", title or ""))
        if len(key) >= MIN_TITLE_KEY:
            keys.setdefault(key, []).append(doc_id)
    return keys


def find_references(text: str, keys: dict[str, list[str]]) -> list[Reference]:
    """本文から「規則名＋条項」の組を抜く."""
    flat = _squash(text)
    if not flat:
        return []

    # 規則名の出現位置。長い鍵を優先し、入れ子は捨てる
    hits: list[tuple[int, str]] = []
    for key in sorted(keys, key=len, reverse=True):
        at = flat.find(key)
        while at != -1:
            if not any(s <= at < s + len(k) for s, k in hits):
                hits.append((at, key))
            at = flat.find(key, at + 1)
    hits.sort()

    def key_near(pos: int) -> str | None:
        """条項の手前 NEAR_WINDOW 文字以内にある規則名."""
        best = None
        for start, key in hits:
            if start < pos:
                if pos - (start + len(key)) <= NEAR_WINDOW:
                    best = key
            else:
                break
        return best

    refs: list[Reference] = []
    seen: set[Reference] = set()
    for m in _ARTICLE.finditer(flat):
        key = key_near(m.start())
        if not key:
            continue
        num = unicodedata.normalize("NFKC", m.group(1))
        ref = Reference(key, num, f"{key} 第{num}条")
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)
    for m in _DOTTED.finditer(flat):
        key = key_near(m.start())
        if not key:
            continue
        ref = Reference(key, m.group(1), f"{key} {m.group(1)}")
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def _search(
    con: sqlite3.Connection,
    doc_ids: list[str],
    match: str,
    prefix: str,
    exclude: set[int],
    limit: int,
) -> list[int]:
    holes = ",".join("?" * len(doc_ids))
    where = f"d.doc_id IN ({holes})"
    params: list[object] = [match, *doc_ids]
    where += " AND (squash(ch.heading) LIKE ? OR squash(ch.heading) LIKE ?)"
    params += [f"{prefix}.%", f"第{prefix}条%"]
    rows = con.execute(
        "SELECT ch.id, ch.heading FROM chunks_fts"
        " JOIN chunks ch ON ch.id = chunks_fts.rowid"
        " JOIN docs d ON d.doc_id = ch.doc_id"
        f" WHERE chunks_fts MATCH ? AND {where}"
        " ORDER BY bm25(chunks_fts) LIMIT ?",
        (*params, limit + len(exclude) + 10),
    ).fetchall()
    out: list[int] = []
    for cid, heading in rows:
        if cid in exclude:
            continue
        flat = _squash(heading)
        if len(flat) > HEADING_MAX or any(ch in flat for ch in _NOT_A_HEADING):
            continue  # 折り返した本文を見出しと誤検出したもの
        out.append(cid)
        if len(out) >= limit:
            break
    return out


def _article_head(
    con: sqlite3.Connection,
    doc_ids: list[str],
    prefix: str,
    exclude: set[int],
    anchor: int,
) -> int | None:
    """条の先頭のチャンク（6.1 など）.

    質問の語で並べると条の総則が落ちることがある。「RJ車両のロールバーは
    ボルトオンでよいか」では、答えの前提になる 6.1）全般（「6.2 以降に従い
    製作」か「JAF/ASN が公認したもの」かの選択制）に質問の語が 1 つも
    含まれず、圏外になった。条の入口は常に渡す。

    ただし同じ番号は章ごとに繰り返す（第1編には 6.1）ホイールも 6.1）全般も
    ある）。**一致したチャンクの直前にあるもの**を選ぶことで章を特定する。
    `anchor` には一致したチャンクの最小 id を渡す。
    """
    holes = ",".join("?" * len(doc_ids))
    rows = con.execute(
        f"SELECT ch.id, ch.heading FROM chunks ch WHERE ch.doc_id IN ({holes})"
        " AND ch.id <= ?"
        " AND (squash(ch.heading) LIKE ? OR squash(ch.heading) LIKE ?)"
        " ORDER BY ch.id DESC",
        (*doc_ids, anchor, f"{prefix}.1%", f"第{prefix}条%"),
    ).fetchall()
    for cid, heading in rows:
        flat = _squash(heading)
        if cid in exclude or len(flat) > HEADING_MAX:
            continue
        if any(ch in flat for ch in _NOT_A_HEADING):
            continue
        return cid
    return None


def follow(
    con: sqlite3.Connection,
    texts: list[str],
    match: str,
    exclude: set[int],
    limit: int = MAX_FOLLOWED,
) -> list[tuple[int, str]]:
    """一段目の本文から参照をたどり、(チャンク id, 参照のラベル) を返す.

    `match` は質問から作った FTS 式。参照先の中で**質問に近い条文**を選ぶ
    ために使う。条番号の前置きで当たらなければ、規則名だけに絞って引く。
    """
    if not match:
        return []
    con.create_function("squash", 1, _squash)
    keys = title_keys(con)
    picked: list[tuple[int, str]] = []
    seen = set(exclude)

    for text in texts:
        for ref in find_references(text, keys):
            doc_ids = keys.get(ref.doc_key) or []
            if not doc_ids:
                continue
            # 当てられなければ足さない（上の docstring の理由）
            ids = _search(con, doc_ids, match, ref.prefix, seen, PER_REFERENCE)
            if not ids:
                continue
            head = _article_head(con, doc_ids, ref.prefix, seen, min(ids))
            for cid in ([head] if head else []) + ids:
                if cid in seen:
                    continue
                seen.add(cid)
                picked.append((cid, ref.label))
                if len(picked) >= limit:
                    return picked
    return picked
