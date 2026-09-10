#!/usr/bin/env python3
"""JAF の公示を規則に紐づける。

`data/announcements.json`（公示）と `data/state.json`（我々の規則）を
規則名で突き合わせ、`data/announcement_links.json` を作ります。

これがあると規則ページから

* その規則に関する公示（日付・公示 No.・JAF のページ）
* **対比表 PDF**（JAF 自身が新旧を並べたもの）

へ案内できます。自動差分だけを見せるより、JAF の対比表に誘導できるほうが
利用者にとって確かです。

## 突き合わせは「添付の文言」を優先する

公示のタイトルより添付ファイルの文言のほうが規則名を正確に含みます。
2025-03-31 の公示（No.2025-WEB025）は 1 件で **20 本の新旧対照表**を
抱えており、タイトル（「新システム稼働に伴う国内競技規則細則等の一部改正
について」）だけでは個々の規則に結びつけられません。添付側を見れば
「自動車競技の組織に関する規定_一部改正（新旧対照表）」と分かります。

    python pipeline/link_announcements.py
    python pipeline/link_announcements.py --report   # 突き合わせ結果を目で見る
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jafreg.announcements import match_titles, normalize_title  # noqa: E402
from jafreg.series import series_key  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  読めません: {path.name}: {exc}")
        return default


def series_titles(docs_state: dict[str, dict[str, Any]]) -> dict[str, str]:
    """{正規化した規則名: 系列キー}。

    同じ系列の年度版はどれも同じ名前に畳まれるので、系列キーに寄せる。
    """
    out: dict[str, str] = {}
    for doc_id, rec in docs_state.items():
        if rec.get("removedAt"):
            continue
        name = normalize_title(rec.get("title") or doc_id)
        if len(name) < 6:
            continue
        # 同名が複数あれば系列キーは同じはず。違えば先に入ったものを残す。
        out.setdefault(name, series_key(doc_id))
    return out


def build(data: Path = DATA) -> dict[str, Any]:
    announcements = _load(data / "announcements.json", {}).get("items") or []
    docs_state = (_load(data / "state.json", {}).get("docs")) or {}
    titles = series_titles(docs_state)

    links: dict[str, list[dict[str, Any]]] = {}
    unmatched: list[dict[str, Any]] = []

    for ann in announcements:
        if not ann.get("ruleChange"):
            continue

        # 添付ごとに規則を決める。1 件の公示が複数の規則にかかることがある。
        per_series: dict[str, list[dict[str, Any]]] = {}
        for att in ann.get("attachments") or []:
            key = match_titles(att.get("text") or "", titles)
            if key:
                per_series.setdefault(key, []).append(att)

        # 添付から決まらなければタイトルで拾う
        if not per_series:
            key = match_titles(ann.get("title") or "", titles)
            if key:
                per_series[key] = list(ann.get("attachments") or [])

        if not per_series:
            unmatched.append(ann)
            continue

        for key, atts in per_series.items():
            links.setdefault(key, []).append(
                {
                    "id": ann["id"],
                    "date": ann.get("date"),
                    "noticeNo": ann.get("noticeNo"),
                    "title": ann.get("title"),
                    "url": ann.get("url"),
                    # この規則に関係する添付だけを持たせる
                    "attachments": atts,
                    "hasComparison": any(a.get("comparison") for a in atts),
                }
            )

    for entries in links.values():
        entries.sort(key=lambda e: (e.get("date") or "", e.get("noticeNo") or ""), reverse=True)

    return {
        "generatedAt": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "series": links,
        "unmatchedCount": len(unmatched),
        "unmatched": [
            {"date": u.get("date"), "noticeNo": u.get("noticeNo"), "title": u.get("title")}
            for u in unmatched
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="公示を規則に紐づける")
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--out", default=str(DATA / "announcement_links.json"))
    ap.add_argument("--report", action="store_true", help="突き合わせ結果を目で見る")
    args = ap.parse_args()

    data = Path(args.data)
    result = build(data)

    if args.report:
        docs_state = (_load(data / "state.json", {}).get("docs")) or {}
        by_series: dict[str, str] = {}
        for doc_id, rec in docs_state.items():
            by_series.setdefault(series_key(doc_id), rec.get("title") or doc_id)

        print(f"=== 紐づいた系列 {len(result['series'])} 件 ===")
        for key, entries in sorted(result["series"].items()):
            comp = sum(1 for e in entries if e["hasComparison"])
            print(f"■ {by_series.get(key, key)}")
            print(f"   系列 {key} / 公示 {len(entries)} 件 / 対比表 {comp} 件")
            for e in entries[:4]:
                mark = " ★対比表" if e["hasComparison"] else ""
                print(f"     {e['date']} No.{e['noticeNo']} {e['title'][:44]}{mark}")
        print()
        print(f"=== 規則に紐づかなかった公示 {result['unmatchedCount']} 件 ===")
        for u in result["unmatched"][:25]:
            print(f"   {u['date']} No.{u['noticeNo']} {u['title'][:60]}")
        return 0

    Path(args.out).write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    comp = sum(
        1 for entries in result["series"].values() for e in entries if e["hasComparison"]
    )
    print(
        f"{len(result['series'])} 系列に紐づけ / 対比表つき {comp} 件 / "
        f"未紐づけ {result['unmatchedCount']} 件 → {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
