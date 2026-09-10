#!/usr/bin/env python3
"""条単位の改正差分を算出して置く。

比較の相手（base）は 2 種類あります。

1. **同じ規則の前の版** … `content/<docId>/previous.json`
   JAF が同じファイル名で差し替えたとき、`cli.py sync` が上書き前に
   退避したもの。これが「改正差分」の本来の姿です。
2. **同じ系列の前年度版** … `data/history.json` の系列から引く
   JAF は年度が変わると別ファイルで公開するため、両方が同時に手元に
   あります。「2027 年版で何が変わったか」がすぐ出せるのはこちらです。

出力は差分の本体と一覧に分けます。

    content/<docId>/diff-<baseDocId>.json   本体（イメージに焼き込まれる）
    data/diffs.json                          一覧（build_index が docs に取り込む）

本体を `content/` に置くのは、Docker の build context に `content/` が
そのまま入るためです（`data/*` は除外設定なので、通したいものを個別に
書く必要がある）。

    python pipeline/build_diffs.py
    python pipeline/build_diffs.py --only jaf_sport_reg_race_20270101-01-9289b7
    python pipeline/build_diffs.py --print <docId>
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jafreg.clausediff import diff_documents  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONTENT = ROOT / "content"

PREVIOUS_NAME = "previous.json"


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  読めません: {path}: {exc}")
        return None


def _pairs(content: Path, history: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(targetDocId, baseDocId, 比較の種別) を集める."""
    out: list[tuple[str, str, str]] = []

    # 1. 同じ規則の前の版
    for doc_dir in sorted(content.glob("*/")):
        if (doc_dir / PREVIOUS_NAME).exists() and (doc_dir / "document.json").exists():
            out.append((doc_dir.name, f"{doc_dir.name}#previous", "revision"))

    # 2. 同じ系列の前年度版（施行日の古い順に隣どうし）
    for members in (history.get("series") or {}).values():
        for older, newer in zip(members, members[1:]):
            if (content / newer["docId"] / "document.json").exists() and (
                content / older["docId"] / "document.json"
            ).exists():
                out.append((newer["docId"], older["docId"], "edition"))
    return out


def _base_document(content: Path, base_id: str) -> dict[str, Any] | None:
    if base_id.endswith("#previous"):
        return _load(content / base_id.removesuffix("#previous") / PREVIOUS_NAME)
    return _load(content / base_id / "document.json")


def _file_name(base_id: str) -> str:
    # "<docId>#previous" はファイル名にできないので previous に落とす
    tag = "previous" if base_id.endswith("#previous") else base_id
    return f"diff-{tag}.json"


def build(content: Path = CONTENT, data: Path = DATA) -> dict[str, Any]:
    history = _load(data / "history.json") or {}
    index: dict[str, list[dict[str, Any]]] = {}
    written = failed = 0

    for target_id, base_id, kind in _pairs(content, history):
        new_doc = _load(content / target_id / "document.json")
        old_doc = _base_document(content, base_id)
        if new_doc is None or old_doc is None:
            failed += 1
            continue

        result = diff_documents(old_doc, new_doc)
        result["kind"] = kind
        result["generatedAt"] = _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        )
        out_path = content / target_id / _file_name(base_id)
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        written += 1

        index.setdefault(target_id, []).append(
            {
                "baseDocId": base_id,
                "baseTitle": result["base"]["title"],
                "baseUploadDate": result["base"]["uploadDate"],
                "kind": kind,
                "file": out_path.name,
                "summary": result["summary"],
            }
        )

    for entries in index.values():
        entries.sort(key=lambda e: (e["kind"] != "revision", e["baseUploadDate"] or ""))

    payload = {
        "generatedAt": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "docs": index,
    }
    (data / "diffs.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    payload["_written"] = written  # type: ignore[assignment]
    payload["_failed"] = failed  # type: ignore[assignment]
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="条単位の改正差分を算出する")
    ap.add_argument("--content", default=str(CONTENT))
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--print", dest="show", default=None, help="docId の差分を出して終わる")
    args = ap.parse_args()

    content = Path(args.content)
    data = Path(args.data)

    if args.show:
        history = _load(data / "history.json") or {}
        found = False
        for target_id, base_id, kind in _pairs(content, history):
            if target_id != args.show:
                continue
            found = True
            new_doc = _load(content / target_id / "document.json")
            old_doc = _base_document(content, base_id)
            if new_doc is None or old_doc is None:
                print(f"  文書が読めません: {base_id}")
                continue
            d = diff_documents(old_doc, new_doc)
            s = d["summary"]
            label = {"revision": "前の版", "edition": "前年度版"}[kind]
            print(f"■ {label}: {d['base']['title']} → {d['target']['title']}")
            print(
                f"   条項 {s['clauses']} / 変更 {s['changed']} / 年号のみ {s['yearOnly']}"
                f" / 追加 {s['added']} / 削除 {s['removed']} / 変更なし {s['unchanged']}"
            )
            for c in d["changes"]:
                tag = {
                    "changed": "変更",
                    "year_only": "年号のみ",
                    "added": "追加",
                    "removed": "削除",
                }[c["status"]]
                print(f"   [{tag}] {c['key']}  {c['heading'][:40]}")
        if not found:
            print(f"比較できる相手がありません: {args.show}")
            return 1
        return 0

    payload = build(content, data)
    docs = payload["docs"]
    total = sum(len(v) for v in docs.values())
    print(
        f"{len(docs)} 文書 / 差分 {total} 件"
        + (f" / 失敗 {payload['_failed']}" if payload["_failed"] else "")
        + f" → {data / 'diffs.json'}"
    )
    for doc_id, entries in sorted(docs.items()):
        for e in entries:
            s = e["summary"]
            print(
                f"  {doc_id}  ← {e['baseDocId']}  "
                f"変更 {s['changed']} / 年号のみ {s['yearOnly']} / "
                f"追加 {s['added']} / 削除 {s['removed']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
