"""1 本の PDF を構造化ドキュメント（JSON + 図版 + HTML）に変換する."""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from . import __version__
from .layout import (
    Block,
    PageAnalysis,
    analyze_page,
    detect_running_texts,
    estimate_body_size,
)
from .render import render_html

FIGURE_DPI = 200
FIGURE_MAX_PX = 1600


@dataclass
class ConvertResult:
    doc_id: str
    page_count: int
    char_count: int
    figure_count: int
    table_count: int
    ocr_pages: list[int]
    warnings: list[str]
    out_dir: Path


def convert_pdf(
    pdf_path: Path,
    out_dir: Path,
    *,
    meta: dict[str, Any],
    ocr: bool = False,
    figure_dpi: int = FIGURE_DPI,
) -> ConvertResult:
    doc_id = meta["doc_id"]
    out_dir = Path(out_dir)
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    ocr_pages: list[int] = []

    with pymupdf.open(pdf_path) as doc:
        if doc.is_encrypted and not doc.authenticate(""):
            raise ValueError("暗号化された PDF を開けません")

        body_size = estimate_body_size(doc)
        drop_texts = detect_running_texts(doc, body_size=body_size)

        analyses: list[PageAnalysis] = []
        for page in doc:
            pa = analyze_page(page, body_size=body_size, drop_texts=drop_texts)
            if pa.needs_ocr:
                if ocr:
                    pa = _ocr_page(page, body_size, drop_texts)
                    ocr_pages.append(page.number)
                else:
                    warnings.append(
                        f"p{page.number + 1}: テキスト層が無い/文字化けの疑い"
                        f"（文字数={pa.char_count}, 化け率={pa.garbage_ratio:.2f}）"
                    )
            analyses.append(pa)

        blocks: list[dict[str, Any]] = []
        toc: list[dict[str, Any]] = []
        fig_n = table_n = char_n = 0
        used_anchors: set[str] = set()

        for pa in analyses:
            page_obj = doc[pa.page]
            fig_index = 0
            for b in pa.blocks:
                if b.type == "figure":
                    fig_index += 1
                    fig_n += 1
                    name = f"fig-p{pa.page + 1:04d}-{fig_index:02d}.webp"
                    _render_figure(page_obj, b.bbox, assets_dir / name, dpi=figure_dpi)
                    b.asset = f"assets/{name}"
                elif b.type == "table":
                    table_n += 1
                elif b.type in ("paragraph", "heading", "caption"):
                    char_n += len(b.text)

                item = _block_to_dict(b, pa)
                if b.type == "heading":
                    anchor = _make_anchor(b.text, used_anchors)
                    item["id"] = anchor
                    toc.append(
                        {"id": anchor, "level": b.level or 3, "text": b.text, "page": pa.page + 1}
                    )
                blocks.append(item)

        page_count = len(doc)

    document = {
        **{k: v for k, v in meta.items() if k != "doc_id"},
        "docId": doc_id,
        "pageCount": page_count,
        "pipelineVersion": __version__,
        "convertedAt": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "stats": {
            "chars": char_n,
            "figures": fig_n,
            "tables": table_n,
            "ocrPages": ocr_pages,
        },
        "warnings": warnings,
        "toc": toc,
        "blocks": blocks,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "document.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out_dir / "index.html").write_text(render_html(document), encoding="utf-8")

    return ConvertResult(
        doc_id=doc_id,
        page_count=page_count,
        char_count=char_n,
        figure_count=fig_n,
        table_count=table_n,
        ocr_pages=ocr_pages,
        warnings=warnings,
        out_dir=out_dir,
    )


def _block_to_dict(b: Block, pa: PageAnalysis) -> dict[str, Any]:
    d: dict[str, Any] = {
        "type": b.type,
        "page": pa.page + 1,
        "bbox": [round(v, 1) for v in b.bbox],
    }
    if b.text:
        d["text"] = b.text
    if b.level:
        d["level"] = b.level
    if b.asset:
        d["asset"] = b.asset
    if b.caption:
        d["caption"] = b.caption
    if b.rows:
        d["rows"] = b.rows
    if b.meta.get("clause"):
        d["clause"] = b.meta["clause"]
    return d


def _render_figure(page: pymupdf.Page, bbox, dest: Path, *, dpi: int) -> None:
    """図版領域をページからクリップしてラスタライズする.

    ベクタ図・ラスタ図・図中の文字をまとめて 1 枚にできるので、
    PDF と同じ見た目のまま HTML に置ける。
    """
    rect = pymupdf.Rect(*bbox) & page.rect
    scale = dpi / 72.0
    longest = max(rect.width, rect.height) * scale
    if longest > FIGURE_MAX_PX:
        scale *= FIGURE_MAX_PX / longest
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect, alpha=False)
    try:
        pix.pil_save(dest, format="WEBP", quality=82, method=4)
    except Exception:  # pragma: no cover - Pillow 未導入時
        pix.save(dest.with_suffix(".png"))


def _ocr_page(page: pymupdf.Page, body_size: float, drop_texts) -> PageAnalysis:
    """テキスト層が無いページを OCR して解析し直す（tesseract + jpn が必要）."""
    tp = page.get_textpage_ocr(language="jpn+eng", dpi=300, full=True)
    raw = page.get_text("dict", textpage=tp)
    page_rect = tuple(page.rect)
    blocks: list[Block] = []
    chars = 0
    for blk in raw.get("blocks", []):
        if blk.get("type") != 0:
            continue
        text = " ".join(
            span.get("text", "")
            for line in blk.get("lines", [])
            for span in line.get("spans", [])
        ).strip()
        if not text:
            continue
        chars += len(text)
        blocks.append(
            Block(type="paragraph", page=page.number, bbox=tuple(blk["bbox"]), text=text)
        )
    return PageAnalysis(
        page=page.number,
        width=page_rect[2],
        height=page_rect[3],
        blocks=blocks,
        char_count=chars,
        garbage_ratio=0.0,
        needs_ocr=False,
    )


_ANCHOR_CLEAN = re.compile(r"[^0-9A-Za-z一-鿿ぁ-ヿー]+")


def _make_anchor(text: str, used: set[str]) -> str:
    base = _ANCHOR_CLEAN.sub("-", text.strip())[:48].strip("-") or "sec"
    anchor = base
    i = 2
    while anchor in used:
        anchor = f"{base}-{i}"
        i += 1
    used.add(anchor)
    return anchor
