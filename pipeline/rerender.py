#!/usr/bin/env python3
"""`document.json` から単体 HTML を作り直す（PDF を読み直さない）。

`content/<docId>/index.html` は変換時に生成されるので、注意書きや
スタイルだけを直したときも、そのままでは既存の 160 本に反映されません。
かといって `cli.py reconvert` は PDF を全部読み直すため 7 分かかり、
`document.json` の `convertedAt` まで書き換わって差分が膨らみます。

このスクリプトは **`document.json` → `index.html` の変換だけ**をやり直します。
PyMuPDF も原本 PDF も要らず、標準ライブラリだけで数秒で終わります。

    python pipeline/rerender.py            # 全件
    python pipeline/rerender.py --only <docId> ...
    python pipeline/rerender.py --dry-run  # 変わる件数だけ数える
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jafreg.render import render_html  # noqa: E402  (sys.path 調整のあとに読む)

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"


def main() -> int:
    ap = argparse.ArgumentParser(description="document.json から index.html を作り直す")
    ap.add_argument("--content", default=str(CONTENT))
    ap.add_argument("--only", nargs="*", default=None, help="docId を指定（既定は全件）")
    ap.add_argument("--dry-run", action="store_true", help="書き込まず、変わる件数だけ出す")
    args = ap.parse_args()

    content_dir = Path(args.content)
    if not content_dir.exists():
        print(f"content ディレクトリがありません: {content_dir}")
        return 1

    targets = sorted(content_dir.glob("*/document.json"))
    if args.only:
        wanted = set(args.only)
        targets = [t for t in targets if t.parent.name in wanted]
        missing = wanted - {t.parent.name for t in targets}
        if missing:
            print(f"見つかりません: {', '.join(sorted(missing))}")
            return 1

    changed = skipped = failed = 0
    for doc_json in targets:
        out = doc_json.parent / "index.html"
        try:
            document = json.loads(doc_json.read_text(encoding="utf-8"))
            html = render_html(document)
        except Exception as exc:  # noqa: BLE001 - 1 本の失敗で全体を止めない
            print(f"  失敗 {doc_json.parent.name}: {type(exc).__name__}: {exc}")
            failed += 1
            continue

        if out.exists() and out.read_text(encoding="utf-8") == html:
            skipped += 1
            continue
        if not args.dry_run:
            out.write_text(html, encoding="utf-8")
        changed += 1

    verb = "変わる" if args.dry_run else "更新"
    print(f"{len(targets)} 文書 / {verb} {changed} 件 / 同一 {skipped} 件 / 失敗 {failed} 件")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
