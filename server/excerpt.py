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

import re
import unicodedata

# ハイライトは HTML ではなく制御文字で囲んで返す。規則本文には "<" が
# 現れうるので、HTML を組み立てて返すと受け取り側でエスケープの判断が
# 必要になり事故のもとになる。
HIGHLIGHT_START = ""
HIGHLIGHT_END = ""

BEFORE = 120
AFTER = 220

# 行頭に現れる条項の目印。変換後の本文はブロックごとに 1 行になっている。
#   第12条　… / 第２章　… / 8.2.4)　… / 5.14)　…
#
# **目印の直後に空白を要求する**のが要点。要求しないと誤検出が出た（実測）:
#   「9)のうちの1つ」            … 文の途中で折り返した行
#   「第253条4に合致しなければ…」  … 同上
# 表を平坦化した行（セルを " | " で繋いだもの）も条項ではないので外す:
#   「031) | オリジナル車両」「211) | 主要寸法」
_CLAUSE_AT_LINE_START = re.compile(
    r"^(?:"
    r"第\s*[0-9０-９]+\s*[条章編節項]"
    r"|[0-9０-９]+(?:[.．][0-9０-９]+)*\s*[)）]"
    r")[\s\u3000]"
)
_TABLE_ROW = " | "
# 拾った行から見出しとして出す長さ
CLAUSE_LABEL_MAX = 42


def clause_at(text: str, offset: int, heading: str | None = None) -> str | None:
    """一致箇所の直前にある条項の行を返す（見出しの検出が効かない規則向け）.

    見出しが 20 ページ分の本文を抱えている場合、`heading` は一致箇所の条では
    ない。ただし本文そのものには「8.2.4)　サイドロールバー…」のように条項の
    目印が残っているので、一致位置から**手前に向かって**探せば実際の条が分かる。
    引き継いだ見出しを出すより、こちらのほうが当たる。
    """
    if not text:
        return None
    found: str | None = None
    at = 0
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if at > offset:
            break
        at += len(line) + 1
        stripped = line.strip()
        # 先頭行は引き継いだ見出しそのもの（チャンクの本文は見出し + 本文）。
        # それを拾ってしまうと、直したはずの間違いをそのまま出すことになる。
        if i == 0 and heading and stripped == heading.strip():
            continue
        if _TABLE_ROW in line:
            continue
        if _CLAUSE_AT_LINE_START.match(unicodedata.normalize("NFKC", stripped)):
            found = stripped
    if not found:
        return None
    label = re.sub(r"\s+", " ", found).strip()
    return label[:CLAUSE_LABEL_MAX] + ("…" if len(label) > CLAUSE_LABEL_MAX else "")


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


def first_match(text: str, terms: list[str]) -> int | None:
    """最初に一致した語の、原文での文字位置.

    チャンク 1 つが 20 ページ分の本文を抱えることがあるので、原本 PDF の
    どのページを開けばよいかは「チャンクの先頭ページ」ではなくここから
    引く（`chunks.pages` の対応表と合わせて使う）。
    """
    if not text:
        return None
    normalized, back = norm_map(text)
    haystack = normalized.casefold()
    best: int | None = None
    for term in terms:
        needle = unicodedata.normalize("NFKC", term).casefold()
        if not needle:
            continue
        i = haystack.find(needle)
        if i == -1:
            continue
        at = back[i] if i < len(back) else len(text)
        if best is None or at < best:
            best = at
    return best


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
