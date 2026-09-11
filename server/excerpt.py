"""検索結果の抜粋づくり.

なぜ独立した小さなモジュールにしてあるか
----------------------------------------
* FastAPI にも numpy にも依存しないので、CI（pipeline の依存だけを入れる
  ジョブ）からテストできる
* ここは位置合わせの細工が入っていて壊れやすい。単体で試せる形にしておく

抜粋の方針
----------
FTS5 の `snippet()` は trigram トークナイザだと 64 トークン＝実質 64 文字が
上限で、「拾った条文を並べて確認する」には短すぎた。一方でチャンク全文
（中央値 510 字）を返すと条をそのまま載せることになる。該当箇所を判断できる
長さだけ切り出し、前後や全文は JAF の原本 PDF の該当ページへ送る。
"""

from __future__ import annotations

import unicodedata

# ハイライトは HTML ではなく制御文字で囲んで返す。規則本文には "<" が
# 現れうるので、HTML を組み立てて返すと受け取り側でエスケープの判断が
# 必要になり事故のもとになる。
HIGHLIGHT_START = ""
HIGHLIGHT_END = ""

BEFORE = 120
AFTER = 220


def norm_map(text: str) -> tuple[str, list[int]]:
    """NFKC 正規化した文字列と、その各文字が原文の何文字目かの対応表.

    全角で書かれた原文（「１０６,７００」）を半角のクエリで引けるように
    するには、正規化した側で探す必要がある。一方で画面に出すのは**原文**
    でなければならない（半角に直して見せると規則の表記が変わる。過去に
    casefold を掛けて「ＦＩＡ」が "fia" になった事故がある）。そのため
    位置を原文側に戻せるようにしておく。

    NFKC は厳密には 1 文字ずつ掛けても全体に掛けた結果と一致しないが、
    語の位置を見つける用途では実用上これで足りる。
    """
    out: list[str] = []
    back: list[int] = []
    for i, ch in enumerate(text):
        n = unicodedata.normalize("NFKC", ch)
        out.append(n)
        back.extend([i] * len(n))
    return "".join(out), back


def excerpt(text: str, terms: list[str], before: int = BEFORE, after: int = AFTER) -> str:
    """該当語の前後を切り出し、一致箇所を制御文字で囲む（原文の表記のまま）."""
    if not text:
        return ""
    normalized, back = norm_map(text)
    haystack = normalized.casefold()
    n_text = len(text)

    def span(i: int, length: int) -> tuple[int, int]:
        start = back[i] if i < len(back) else n_text
        j = i + length
        return start, (back[j] if j < len(back) else n_text)

    spans: list[tuple[int, int]] = []
    for term in terms:
        needle = unicodedata.normalize("NFKC", term).casefold()
        if not needle:
            continue
        i = haystack.find(needle)
        while i != -1:
            spans.append(span(i, len(needle)))
            i = haystack.find(needle, i + len(needle))

    if not spans:
        head = text[: before + after]
        return head + (" …" if n_text > len(head) else "")

    spans.sort()
    start = max(0, spans[0][0] - before)
    end = min(n_text, spans[0][0] + after)

    parts: list[str] = []
    cursor = start
    for a, b in spans:
        if a < cursor or b > end:
            continue
        parts.append(text[cursor:a])
        parts.append(HIGHLIGHT_START)
        parts.append(text[a:b])
        parts.append(HIGHLIGHT_END)
        cursor = b
    parts.append(text[cursor:end])

    return ("… " if start > 0 else "") + "".join(parts) + (" …" if end < n_text else "")
