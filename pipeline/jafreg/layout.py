"""PDF ページのレイアウト解析.

このモジュールが「PDF 全文が取れない」「画像が本文の正しい位置に入らない」
という 2 つの課題の中心になる。方針:

1. **本文** は PyMuPDF の `get_text("dict")` でブロック + スパン単位に取り、
   XY-Cut で段組と読み順を復元する。ページヘッダ／フッタは全ページ横断で
   繰り返し出現するものを検出して落とす。

2. **図版** は「埋め込みラスタ画像を取り出す」だけでは足りない。JAF の
   車両規則の図は大半が *ベクタ描画* で、`get_images()` では 1 枚も取れない。
   そこで
     - ラスタ画像の配置矩形 (`get_image_info`)
     - ベクタ描画のクラスタ矩形 (`cluster_drawings`)
   を統合して「図版領域」を決め、その領域を **ページからクリップして
   ラスタライズ** する。これで線画・寸法線・注記が入った図がそのままの
   見た目で取り出せる。

3. **配置** は図版領域を 1 つのブロックとして本文ブロックと同じ読み順
   ソートに混ぜることで決まる。図版の中に落ちた文字ブロックは本文から
   除外する（図の中に描き込まれた文字なので、二重に出さない）。
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Iterable, Literal

import pymupdf

Rect = tuple[float, float, float, float]

BlockType = Literal["heading", "paragraph", "figure", "table", "caption"]

# --- 図版判定のしきい値 -------------------------------------------------
MIN_FIGURE_AREA = 2500.0  # pt^2 … 50x50pt 未満は罫線・記号とみなす
MIN_FIGURE_SIDE = 24.0  # pt
MAX_FIGURE_PAGE_RATIO = 0.92  # ページ枠線を図と誤認しない
FIGURE_MERGE_GAP = 12.0  # pt … これ以下の隙間の領域は 1 つの図にまとめる
FIGURE_PAD = 4.0  # pt … 切り出し時の余白

# --- 見出し判定 ---------------------------------------------------------
HEADING_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^第\s*[0-9０-９一二三四五六七八九十]+\s*編"), 1),
    (re.compile(r"^第\s*[0-9０-９一二三四五六七八九十]+\s*部"), 1),
    (re.compile(r"^第\s*[0-9０-９一二三四五六七八九十]+\s*章"), 2),
    (re.compile(r"^第\s*[0-9０-９一二三四五六七八九十]+\s*節"), 3),
    (re.compile(r"^第\s*[0-9０-９]+\s*条"), 4),
    (re.compile(r"^(附\s*則|付\s*則|別\s*表|別\s*紙|付\s*表|付\s*録|参\s*考)"), 2),
    (re.compile(r"^[0-9０-９]+\s*[-－‐]\s*[0-9０-９]+\s*[-－‐]\s*[0-9０-９]+"), 5),
    (re.compile(r"^[0-9０-９]+\s*[-－‐]\s*[0-9０-９]+"), 5),
    (re.compile(r"^[0-9０-９]+\s*[.．]\s*[0-9０-９]+"), 5),
    (re.compile(r"^[0-9０-９]+\s*[.．]\s*\S"), 5),
]

CAPTION_RE = re.compile(r"^\s*[（(]?\s*(図|表|写真|Fig|Table|参考図|付図)\s*[0-9０-９\-－.]*")

# 本文中の条項番号（見出しにしなくても、引用のために覚えておく）
CLAUSE_RE = re.compile(
    r"^\s*(第\s*[0-9０-９一二三四五六七八九十]+\s*[編章節条]"
    r"|[0-9０-９]+(?:\s*[.．\-－]\s*[0-9０-９]+)*\s*[）)]?)"
)

# 文字化け（CID フォント埋め込み失敗）検出用
_GARBAGE_RE = re.compile(r"[�-]")
_CJK_RE = re.compile(r"[　-ヿ一-鿿]")


@dataclass
class Block:
    type: BlockType
    page: int  # 0-origin
    bbox: Rect
    text: str = ""
    level: int | None = None
    # figure 用
    asset: str | None = None
    caption: str | None = None
    # table 用
    rows: list[list[str | None]] | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class PageAnalysis:
    page: int
    width: float
    height: float
    blocks: list[Block]
    char_count: int
    garbage_ratio: float
    needs_ocr: bool


# ---------------------------------------------------------------------------
# 幾何ユーティリティ
# ---------------------------------------------------------------------------


def _area(r: Rect) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _intersect(a: Rect, b: Rect) -> Rect:
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def _overlap_ratio(inner: Rect, outer: Rect) -> float:
    """inner のうち outer に含まれる面積比."""
    ia = _area(inner)
    if ia <= 0:
        return 0.0
    return _area(_intersect(inner, outer)) / ia


def _union(a: Rect, b: Rect) -> Rect:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _expanded(r: Rect, pad: float) -> Rect:
    return (r[0] - pad, r[1] - pad, r[2] + pad, r[3] + pad)


def _near(a: Rect, b: Rect, gap: float) -> bool:
    return _area(_intersect(_expanded(a, gap), _expanded(b, gap))) > 0


def merge_rects(rects: Iterable[Rect], gap: float = FIGURE_MERGE_GAP) -> list[Rect]:
    """近接する矩形を貪欲にマージする."""
    out: list[Rect] = []
    for r in rects:
        merged = True
        while merged:
            merged = False
            for i, o in enumerate(out):
                if _near(r, o, gap):
                    r = _union(r, o)
                    out.pop(i)
                    merged = True
                    break
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# 図版領域の抽出
# ---------------------------------------------------------------------------


def figure_regions(
    page: pymupdf.Page, table_rects: list[Rect], drawings: list | None = None
) -> list[Rect]:
    page_rect = tuple(page.rect)
    page_area = _area(page_rect)
    candidates: list[Rect] = []

    # (a) 配置済みラスタ画像
    try:
        for info in page.get_image_info():
            bbox = tuple(info["bbox"])
            if _area(bbox) >= MIN_FIGURE_AREA:
                candidates.append(bbox)
    except Exception:  # pragma: no cover - PyMuPDF のバージョン差異
        pass

    # (b) ベクタ描画のクラスタ（JAF の図はほぼこちら）
    if drawings is None:
        try:
            drawings = page.get_drawings()
        except Exception:  # pragma: no cover
            drawings = []
    clusters: list[Rect] = []
    try:
        clusters = [tuple(r) for r in page.cluster_drawings(drawings=drawings)]
    except Exception:  # pragma: no cover
        clusters = [tuple(d["rect"]) for d in drawings]
    candidates.extend(clusters)

    kept: list[Rect] = []
    for r in candidates:
        w, h = r[2] - r[0], r[3] - r[1]
        if w < MIN_FIGURE_SIDE or h < MIN_FIGURE_SIDE:
            continue  # 罫線・下線
        if _area(r) < MIN_FIGURE_AREA:
            continue
        if _area(r) / page_area > MAX_FIGURE_PAGE_RATIO:
            continue  # ページ全体の枠
        # 表として検出済みの領域と大きく重なるものは表に任せる
        if any(_overlap_ratio(r, t) > 0.8 for t in table_rects):
            continue
        kept.append(r)

    return merge_rects(kept)


def is_framed_text(
    region: Rect,
    text_rects: list[Rect],
    drawings: list,
    *,
    text_cover: float = 0.30,
    max_paths: int = 4,
) -> bool:
    """「罫線で囲んだ本文」を図版と誤認しないための判定.

    JAF の規則には ［参考］ のような枠囲みの本文が頻出する。これを図版
    として画像化してしまうと、その中の条文が全文検索にも AI の根拠にも
    出てこなくなる。枠の中が文字で埋まっていて、描画パスが枠線程度しか
    無いものは本文として扱う。
    """
    area = _area(region)
    if area <= 0:
        return False
    inside = sum(_area(t) for t in text_rects if _overlap_ratio(t, region) > 0.7)
    if inside / area < text_cover:
        return False
    paths = sum(1 for d in drawings if _overlap_ratio(tuple(d["rect"]), region) > 0.5)
    return paths <= max_paths


# ---------------------------------------------------------------------------
# XY-Cut による読み順復元
# ---------------------------------------------------------------------------


def xy_cut(blocks: list[Block], *, min_gap_x: float = 18.0, min_gap_y: float = 8.0) -> list[Block]:
    """再帰的 XY-Cut。段組・回り込みのあるページでも読み順を保つ."""
    if len(blocks) <= 1:
        return list(blocks)

    # 縦方向の空白帯（＝段の区切り）を探す
    col_split = _find_gap(blocks, axis=0, min_gap=min_gap_x)
    if col_split is not None:
        left = [b for b in blocks if b.bbox[0] < col_split]
        right = [b for b in blocks if b.bbox[0] >= col_split]
        if left and right:
            return xy_cut(left, min_gap_x=min_gap_x, min_gap_y=min_gap_y) + xy_cut(
                right, min_gap_x=min_gap_x, min_gap_y=min_gap_y
            )

    row_split = _find_gap(blocks, axis=1, min_gap=min_gap_y)
    if row_split is not None:
        top = [b for b in blocks if b.bbox[1] < row_split]
        bottom = [b for b in blocks if b.bbox[1] >= row_split]
        if top and bottom:
            return xy_cut(top, min_gap_x=min_gap_x, min_gap_y=min_gap_y) + xy_cut(
                bottom, min_gap_x=min_gap_x, min_gap_y=min_gap_y
            )

    return sorted(blocks, key=lambda b: (round(b.bbox[1], 1), b.bbox[0]))


def _find_gap(blocks: list[Block], *, axis: int, min_gap: float) -> float | None:
    """axis(0=x,1=y) 方向で、どのブロックも跨がない最大の空白帯の中心を返す."""
    lo = axis
    hi = axis + 2
    spans = sorted((b.bbox[lo], b.bbox[hi]) for b in blocks)
    best: tuple[float, float] | None = None
    cursor = spans[0][1]
    for start, end in spans[1:]:
        gap = start - cursor
        if gap >= min_gap and (best is None or gap > best[1]):
            best = (cursor + gap / 2, gap)
        cursor = max(cursor, end)
    return best[0] if best else None


# ---------------------------------------------------------------------------
# テキスト整形
# ---------------------------------------------------------------------------


def join_lines(lines: list[str]) -> str:
    """日本語は詰めて、欧文は空白を入れて連結する."""
    out = ""
    for line in lines:
        line = line.rstrip()
        if not out:
            out = line
            continue
        prev, nxt = out[-1:], line[:1]
        if _CJK_RE.search(prev) or _CJK_RE.search(nxt) or prev in "、。）」":
            out += line
        else:
            out += " " + line
    return out.strip()


def classify_heading(text: str, size: float, body_size: float, bold: bool) -> int | None:
    """見出しレベルを返す（本文なら None）."""
    stripped = text.strip()
    if not stripped or len(stripped) > 120:
        return None
    for pattern, level in HEADING_PATTERNS:
        m = pattern.match(stripped)
        if not m:
            continue
        # 「9.4）…」のような項番は、その行がそのまま条文本体であることが多い。
        # 短いものだけを見出しとして扱い、長いものは本文のままにする。
        if level >= 5 and len(stripped) > 30:
            return None
        return level
    if size >= body_size * 1.45:
        return 1
    if size >= body_size * 1.22:
        return 2
    if bold and size >= body_size * 1.05 and len(stripped) <= 40:
        return 3
    return None


# ---------------------------------------------------------------------------
# ページ解析本体
# ---------------------------------------------------------------------------


def analyze_page(
    page: pymupdf.Page,
    *,
    body_size: float,
    drop_texts: set[str] | None = None,
    detect_tables: bool = True,
) -> PageAnalysis:
    page_rect = tuple(page.rect)
    drop_texts = drop_texts or set()
    page_height = page_rect[3]

    # --- 表 ---------------------------------------------------------------
    table_blocks: list[Block] = []
    table_rects: list[Rect] = []
    if detect_tables:
        try:
            for t in page.find_tables().tables:
                rect = tuple(t.bbox)
                if _area(rect) < MIN_FIGURE_AREA:
                    continue
                table_rects.append(rect)
                table_blocks.append(
                    Block(
                        type="table",
                        page=page.number,
                        bbox=rect,
                        rows=t.extract(),
                        meta={"cols": t.col_count, "rows_n": t.row_count},
                    )
                )
        except Exception:  # pragma: no cover
            table_rects = []
            table_blocks = []

    # --- 図版 -------------------------------------------------------------
    try:
        drawings = page.get_drawings()
    except Exception:  # pragma: no cover
        drawings = []
    raw_dict = page.get_text("dict")
    text_rects = [
        tuple(b["bbox"]) for b in raw_dict.get("blocks", []) if b.get("type") == 0
    ]
    fig_rects = [
        r
        for r in figure_regions(page, table_rects, drawings=drawings)
        if not is_framed_text(r, text_rects, drawings)
    ]
    figure_blocks = [
        Block(type="figure", page=page.number, bbox=r, meta={"clip": r}) for r in fig_rects
    ]

    # --- 本文 -------------------------------------------------------------
    raw = raw_dict
    text_blocks: list[Block] = []
    char_count = 0
    garbage = 0

    for blk in raw.get("blocks", []):
        if blk.get("type") != 0:
            continue
        bbox = tuple(blk["bbox"])
        lines: list[str] = []
        sizes: list[tuple[float, int]] = []
        bold_chars = 0
        total_chars = 0
        for line in blk.get("lines", []):
            parts = []
            for span in line.get("spans", []):
                s = span.get("text", "")
                if not s:
                    continue
                parts.append(s)
                n = len(s)
                total_chars += n
                sizes.append((span.get("size", body_size), n))
                if span.get("flags", 0) & 2 ** 4:  # bold
                    bold_chars += n
                garbage += len(_GARBAGE_RE.findall(s))
            if parts:
                lines.append("".join(parts))
        # 行頭の字下げ（全角スペース）は段落の開始記号。join_lines の strip で
        # 消えてしまうため、ここで拾っておく。
        indented = bool(lines) and lines[0][:1] in ("\u3000", " ", "\u00a0")
        text = join_lines(lines)
        char_count += total_chars
        if not text.strip():
            continue

        # ヘッダ／フッタ（天地の余白域に、複数ページで同じ文字列が出るもの）を落とす
        in_margin = bbox[3] < page_height * MARGIN_RATIO or bbox[1] > page_height * (1 - MARGIN_RATIO)
        # 図・表の内側の文字は図側に含まれるので本文からは外す
        if any(_overlap_ratio(bbox, r) > 0.7 for r in fig_rects):
            continue
        if any(_overlap_ratio(bbox, r) > 0.7 for r in table_rects):
            continue

        size = statistics.median([s for s, n in sizes for _ in range(max(1, n // 4))]) if sizes else body_size
        if in_margin and size <= body_size * 1.15 and normalize_running(text) in drop_texts:
            continue
        bold = total_chars > 0 and bold_chars / total_chars > 0.6
        level = classify_heading(text, size, body_size, bold)
        meta = {"size": round(size, 2), "bold": bold, "indent": indented}
        clause = CLAUSE_RE.match(text)
        if clause:
            meta["clause"] = re.sub(r"\s+", "", clause.group(1))
        text_blocks.append(
            Block(
                type="heading" if level else "paragraph",
                page=page.number,
                bbox=bbox,
                text=text,
                level=level,
                meta=meta,
            )
        )

    ordered = xy_cut(text_blocks + figure_blocks + table_blocks)
    ordered = merge_paragraph_blocks(ordered, body_size)
    _attach_captions(ordered)

    garbage_ratio = garbage / char_count if char_count else 0.0
    # スキャン PDF は「文字がほぼ無い」かつ「ラスタ画像がページの大半を覆う」。
    # 単に文字数の少ない扉ページを OCR 対象にしないための条件。
    raster_cover = 0.0
    try:
        raster_cover = sum(_area(tuple(i["bbox"])) for i in page.get_image_info()) / _area(page_rect)
    except Exception:  # pragma: no cover
        pass
    needs_ocr = (char_count < 40 and raster_cover > 0.45) or garbage_ratio > 0.2

    return PageAnalysis(
        page=page.number,
        width=page_rect[2],
        height=page_rect[3],
        blocks=ordered,
        char_count=char_count,
        garbage_ratio=garbage_ratio,
        needs_ocr=needs_ocr,
    )



# ---------------------------------------------------------------------------
# 行 → 段落の再構成
# ---------------------------------------------------------------------------

# 箇条書き・条項番号の先頭記号。ここで始まる行は新しい段落として扱う。
_ENUM_RE = re.compile(
    r"^\s*(?:"
    r"[（(]\s*[0-9０-９a-zA-Zａ-ｚア-ンぁ-ん一二三四五六七八九十]{1,3}\s*[）)]"
    r"|[0-9０-９]{1,3}\s*[）)．.、]"
    r"|[①-⑳]|[⑴-⒇]|[ⅰ-ⅹⅠ-Ⅹ]"
    r"|[・･※＊*]|[-－―—]\s"
    r"|第\s*[0-9０-９一二三四五六七八九十]+"
    r")"
)


def merge_paragraph_blocks(blocks: list[Block], body_size: float) -> list[Block]:
    """PDF の「1 行 = 1 ブロック」を人が読む段落に戻す.

    JAF の PDF は行ごとにテキストブロックが切られていることが多く、
    そのまま出すと 1 行ごとに <p> が並んで読めない。加えて RAG の
    チャンク化でも文の途中で切れてしまうため、ここで繋ぎ直す。

    繋ぐ条件（すべて満たすとき）:
      * 直前の行が右マージンまで達している（＝折り返し途中）
      * 行送りとして妥当な縦位置
      * 左端が揃っている（字下げで始まる行は新しい段落）
      * 箇条書き記号・条項番号で始まらない
      * フォントサイズが同じ
    """
    paras = [b for b in blocks if b.type == "paragraph"]
    if len(paras) < 2:
        return blocks
    right_margin = max(b.bbox[2] for b in paras)

    out: list[Block] = []
    for b in blocks:
        if b.type != "paragraph" or not out or out[-1].type != "paragraph":
            out.append(b)
            continue
        prev = out[-1]
        p_size = prev.meta.get("size", body_size)
        b_size = b.meta.get("size", body_size)
        size = max(p_size, b_size)
        gap = b.bbox[1] - prev.bbox[3]
        if not (-0.4 * size <= gap <= 0.9 * size):
            out.append(b)
            continue
        if abs(p_size - b_size) > 0.6:
            out.append(b)
            continue
        # ぶら下げインデント（項番の幅だけ右にずれた継続行）は許容する
        if not (prev.bbox[0] - size * 0.6 <= b.bbox[0] <= prev.bbox[0] + size * 8.0):
            out.append(b)
            continue
        # 直前の行が右マージンに届いていない＝そこで段落が終わっている
        if prev.bbox[2] < right_margin - size * 1.5:
            out.append(b)
            continue
        # 字下げ・箇条書き記号で始まる行は新しい段落
        if b.meta.get("indent") or _ENUM_RE.match(b.text):
            out.append(b)
            continue
        prev.text = join_lines([prev.text, b.text])
        prev.bbox = _union(prev.bbox, b.bbox)
    return out

def _attach_captions(blocks: list[Block]) -> None:
    """図版の直前／直後にあるキャプション行を図に結びつける."""
    for i, b in enumerate(blocks):
        if b.type != "figure":
            continue
        for j in (i + 1, i - 1):
            if not (0 <= j < len(blocks)):
                continue
            cand = blocks[j]
            if cand.type != "paragraph" or not CAPTION_RE.match(cand.text):
                continue
            if abs(cand.bbox[1] - b.bbox[3]) > 60 and abs(b.bbox[1] - cand.bbox[3]) > 60:
                continue
            b.caption = cand.text
            cand.type = "caption"
            cand.meta["belongs_to_figure"] = True
            break


def estimate_body_size(doc: pymupdf.Document, sample_pages: int = 12) -> float:
    """本文の標準文字サイズ（文字数で重み付けした最頻値）を推定する."""
    counter: dict[float, int] = {}
    step = max(1, len(doc) // sample_pages)
    for i in range(0, len(doc), step):
        for blk in doc[i].get_text("dict").get("blocks", []):
            if blk.get("type") != 0:
                continue
            for line in blk.get("lines", []):
                for span in line.get("spans", []):
                    key = round(span.get("size", 0), 1)
                    counter[key] = counter.get(key, 0) + len(span.get("text", ""))
    if not counter:
        return 10.0
    return max(counter.items(), key=lambda kv: kv[1])[0]


MARGIN_RATIO = 0.12  # ページ天地の何割をヘッダ／フッタ候補域とみなすか
_NUM_RE = re.compile(r"[0-9０-９]+")


def normalize_running(text: str) -> str:
    """ページ番号などの可変部分を伏せた比較用キー."""
    return _NUM_RE.sub("#", re.sub(r"\s+", "", text))[:60]


def detect_running_texts(
    doc: pymupdf.Document, threshold: float = 0.5, body_size: float | None = None
) -> set[str]:
    """全ページで繰り返されるヘッダ／フッタ *文字列* を検出する.

    位置だけで落とすと、ページ冒頭に来た「第◯章」などの見出しを
    巻き添えにする。実際に複数ページで同じ文字列が同じ帯に現れる
    ものだけを落とす。
    """
    if len(doc) < 2:
        return set()
    if body_size is None:
        body_size = estimate_body_size(doc)
    counter: dict[str, set[int]] = {}
    for i, page in enumerate(doc):
        h = page.rect.height
        for blk in page.get_text("dict").get("blocks", []):
            if blk.get("type") != 0:
                continue
            y0, y1 = blk["bbox"][1], blk["bbox"][3]
            if not (y1 < h * MARGIN_RATIO or y0 > h * (1 - MARGIN_RATIO)):
                continue
            # 柱・ノンブルは本文より小さい。大きな文字はページ冒頭の見出しなので残す。
            max_size = max(
                (span.get("size", 0) for line in blk.get("lines", []) for span in line.get("spans", [])),
                default=0.0,
            )
            if max_size > body_size * 1.15:
                continue
            text = "".join(
                span.get("text", "")
                for line in blk.get("lines", [])
                for span in line.get("spans", [])
            )
            key = normalize_running(text)
            if key:
                counter.setdefault(key, set()).add(i)

    # 章ごとに文字列が変わる柱もあるため、長い文書では出現ページ数の下限を
    # 4 ページで頭打ちにする。本文より小さいフォントであることを別途要求して
    # いるので、本物の見出しを巻き込む危険は小さい。
    need = max(2, min(4, len(doc) * threshold))
    return {k for k, pages in counter.items() if len(pages) >= need}


# 後方互換のためのエイリアス（旧名）
def detect_running_bands(doc: pymupdf.Document, threshold: float = 0.5) -> set[str]:
    return detect_running_texts(doc, threshold)
