#!/usr/bin/env python3
"""JAF 諸規則 HTML 化パイプライン CLI.

    python pipeline/cli.py crawl                 # カタログだけ更新
    python pipeline/cli.py sync                  # 巡回 → 差分検出 → 変換
    python pipeline/cli.py sync --limit 3        # 先頭 3 件だけ（動作確認用）
    python pipeline/cli.py sync --only <doc_id>
    python pipeline/cli.py convert-one a.pdf --title "テスト"   # ローカル PDF を変換
    python pipeline/cli.py probe a.pdf           # 変換前の診断だけ出す
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jafreg import __version__
from jafreg.catalog import SOURCE_PAGES, CatalogEntry, parse_listing
from jafreg.convert import convert_pdf
from jafreg.fetch import JafClient

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONTENT = ROOT / "content"
CACHE = ROOT / ".cache" / "pdf"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------


def cmd_crawl(args) -> int:
    entries: list[CatalogEntry] = []
    with JafClient(CACHE, delay_sec=args.delay) as client:
        for source, url in SOURCE_PAGES.items():
            print(f"[crawl] {source}: {url}", flush=True)
            html = client.get_text(url)
            found = parse_listing(html, source, url)
            print(f"[crawl]   {len(found)} 件", flush=True)
            entries.extend(found)

    catalog = [e.to_dict() for e in entries]
    _save_json(DATA / "catalog.json", {"generatedAt": _now(), "count": len(catalog), "items": catalog})
    print(f"[crawl] 合計 {len(catalog)} 件 → data/catalog.json")
    return 0


def cmd_sync(args) -> int:
    if cmd_crawl(args) != 0:
        return 1

    catalog = _load_json(DATA / "catalog.json", {"items": []})["items"]
    state = _load_json(DATA / "state.json", {"docs": {}})
    docs_state: dict = state.setdefault("docs", {})

    if args.only:
        catalog = [c for c in catalog if c["doc_id"] in set(args.only)]
    if args.limit:
        catalog = catalog[: args.limit]

    changes = {"generatedAt": _now(), "added": [], "updated": [], "unchanged": 0, "failed": []}
    seen: set[str] = set()

    with JafClient(CACHE, delay_sec=args.delay) as client:
        for i, item in enumerate(catalog, 1):
            doc_id = item["doc_id"]
            seen.add(doc_id)
            prev = docs_state.get(doc_id)
            label = f"[{i}/{len(catalog)}] {item['title'][:40]}"

            # アップロード日が同じ & 変換済み & パイプライン版も同じならスキップ
            if (
                prev
                and not args.force
                and prev.get("uploadDate") == item.get("upload_date")
                and prev.get("pipelineVersion") == __version__
                and (CONTENT / doc_id / "document.json").exists()
            ):
                changes["unchanged"] += 1
                print(f"{label} … skip (日付一致)", flush=True)
                continue

            try:
                fetched = client.download_pdf(item["pdf_url"], doc_id)
            except Exception as exc:
                changes["failed"].append({"docId": doc_id, "stage": "fetch", "error": str(exc)})
                print(f"{label} … FETCH FAILED: {exc}", flush=True)
                continue

            if (
                prev
                and not args.force
                and prev.get("sha256") == fetched.sha256
                and prev.get("pipelineVersion") == __version__
                and (CONTENT / doc_id / "document.json").exists()
            ):
                docs_state[doc_id] = {**prev, "uploadDate": item.get("upload_date")}
                changes["unchanged"] += 1
                print(f"{label} … skip (ハッシュ一致)", flush=True)
                continue

            meta = {
                "doc_id": doc_id,
                "title": item["title"],
                "source": item["source"],
                "sourceUrl": item["source_url"],
                "section": item["section"],
                "group": item["group"],
                "pdfUrl": item["pdf_url"],
                "uploadDate": item.get("upload_date"),
                "sha256": fetched.sha256,
                "bytes": fetched.bytes,
            }
            try:
                res = convert_pdf(
                    fetched.path, CONTENT / doc_id, meta=meta, ocr=args.ocr, figure_dpi=args.dpi
                )
            except Exception as exc:
                changes["failed"].append(
                    {"docId": doc_id, "stage": "convert", "error": f"{exc}\n{traceback.format_exc(limit=3)}"}
                )
                print(f"{label} … CONVERT FAILED: {exc}", flush=True)
                continue

            record = {
                "docId": doc_id,
                "title": item["title"],
                "pdfUrl": item["pdf_url"],
                "uploadDate": item.get("upload_date"),
                "sha256": fetched.sha256,
                "bytes": fetched.bytes,
                "pageCount": res.page_count,
                "chars": res.char_count,
                "figures": res.figure_count,
                "tables": res.table_count,
                "warnings": len(res.warnings),
                "pipelineVersion": __version__,
                "convertedAt": _now(),
            }
            bucket = "updated" if prev else "added"
            changes[bucket].append(
                {
                    "docId": doc_id,
                    "title": item["title"],
                    "uploadDate": item.get("upload_date"),
                    "previousUploadDate": (prev or {}).get("uploadDate"),
                }
            )
            docs_state[doc_id] = record
            print(
                f"{label} … OK "
                f"({res.page_count}p / {res.char_count}字 / 図{res.figure_count} / 表{res.table_count}"
                + (f" / 警告{len(res.warnings)}" if res.warnings else "")
                + ")",
                flush=True,
            )

    if not args.only and not args.limit:
        removed = [d for d in docs_state if d not in seen]
        for d in removed:
            docs_state[d]["removedAt"] = _now()
        changes["removed"] = removed

    state["updatedAt"] = _now()
    state["pipelineVersion"] = __version__
    _save_json(DATA / "state.json", state)
    _save_json(DATA / "changes" / f"{_dt.date.today().isoformat()}.json", changes)
    _write_index(docs_state)

    print(
        f"\n[sync] 追加 {len(changes['added'])} / 更新 {len(changes['updated'])} / "
        f"据置 {changes['unchanged']} / 失敗 {len(changes['failed'])}"
    )
    return 0


def _write_index(docs_state: dict) -> None:
    """フロントエンドが最初に読む軽量インデックス."""
    items = []
    for doc_id, rec in sorted(docs_state.items()):
        if rec.get("removedAt"):
            continue
        doc_path = CONTENT / doc_id / "document.json"
        if not doc_path.exists():
            continue
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        items.append(
            {
                "docId": doc_id,
                "title": doc.get("title"),
                "source": doc.get("source"),
                "section": doc.get("section"),
                "group": doc.get("group"),
                "uploadDate": doc.get("uploadDate"),
                "pdfUrl": doc.get("pdfUrl"),
                "pageCount": doc.get("pageCount"),
                "stats": doc.get("stats"),
                "toc": doc.get("toc", [])[:40],
            }
        )
    _save_json(DATA / "index.json", {"generatedAt": _now(), "count": len(items), "items": items})


def cmd_convert_one(args) -> int:
    pdf = Path(args.pdf)
    doc_id = args.doc_id or pdf.stem
    meta = {
        "doc_id": doc_id,
        "title": args.title or pdf.stem,
        "source": "local",
        "sourceUrl": None,
        "section": "",
        "group": "",
        "pdfUrl": args.pdf_url,
        "uploadDate": None,
    }
    res = convert_pdf(pdf, Path(args.out) / doc_id, meta=meta, ocr=args.ocr, figure_dpi=args.dpi)
    print(
        f"{doc_id}: {res.page_count}ページ / {res.char_count}字 / "
        f"図 {res.figure_count} / 表 {res.table_count}"
    )
    for w in res.warnings[:20]:
        print("  ! " + w)
    print(f"→ {res.out_dir}")
    return 0


def cmd_probe(args) -> int:
    """変換品質を調べるための診断。PDF 本体は持ち出さず統計だけ出す."""
    import pymupdf

    from jafreg.layout import detect_running_texts, estimate_body_size, figure_regions

    out = {"file": Path(args.pdf).name, "pipelineVersion": __version__, "pages": []}
    with pymupdf.open(args.pdf) as doc:
        body = estimate_body_size(doc)
        drop_texts = detect_running_texts(doc)
        out["pageCount"] = len(doc)
        out["bodyFontSize"] = body
        out["runningTexts"] = sorted(drop_texts)
        out["metadata"] = {k: v for k, v in (doc.metadata or {}).items() if v}
        out["fonts"] = sorted({f[3] for p in doc for f in p.get_fonts(full=True)})[:40]
        pages = range(len(doc)) if args.all else range(min(len(doc), args.pages))
        for i in pages:
            page = doc[i]
            text = page.get_text()
            tables = []
            try:
                tables = [list(map(lambda v: round(v, 1), t.bbox)) for t in page.find_tables().tables]
            except Exception:
                pass
            figs = figure_regions(page, [tuple(t) for t in tables])
            out["pages"].append(
                {
                    "page": i + 1,
                    "chars": len(text),
                    "rasterImages": len(page.get_images()),
                    "drawings": len(page.get_drawings()),
                    "figureRegions": [[round(v, 1) for v in f] for f in figs],
                    "tables": tables,
                    "head": text.strip()[:200],
                }
            )
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("file", "pageCount", "bodyFontSize")}, ensure_ascii=False))
    print(f"→ {args.out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="JAF 諸規則 HTML 化パイプライン")
    p.add_argument("--delay", type=float, default=1.5, help="リクエスト間隔（秒）")
    p.add_argument("--dpi", type=int, default=200, help="図版の書き出し解像度")
    p.add_argument("--ocr", action="store_true", help="テキスト層の無いページを OCR する")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("crawl", help="カタログのみ取得")
    sp.set_defaults(func=cmd_crawl)

    sp = sub.add_parser("sync", help="巡回・差分検出・変換")
    sp.add_argument("--limit", type=int, default=0)
    sp.add_argument("--only", nargs="*", default=None)
    sp.add_argument("--force", action="store_true", help="差分に関係なく再変換")
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("convert-one", help="ローカル PDF を 1 本変換")
    sp.add_argument("pdf")
    sp.add_argument("--title")
    sp.add_argument("--doc-id")
    sp.add_argument("--pdf-url")
    sp.add_argument("--out", default=str(CONTENT))
    sp.set_defaults(func=cmd_convert_one)

    sp = sub.add_parser("probe", help="PDF の構造診断")
    sp.add_argument("pdf")
    sp.add_argument("--pages", type=int, default=8)
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--out", default="probe.json")
    sp.set_defaults(func=cmd_probe)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
