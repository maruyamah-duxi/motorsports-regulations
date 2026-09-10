#!/usr/bin/env python3
"""規則ごとの更新履歴（`data/history.json`）を組み立てる。

材料は 2 つ。

* `data/state.json` … 各文書の現在の掲載日・ハッシュ
* `data/changes/YYYY-MM-DD.json` … 巡回ごとの差分（added / updated / removed）

**表示する日付は JAF の掲載日（uploadDate）**にします。こちらが検出した日
ではありません。規則の更新日として意味があるのは JAF 側の日付だからです。

ここでいちばん気をつけているのは、**「JAF が更新した」と「こちらが
再変換した」を混ぜないこと**です。2026-09-09 の差分は 143 件が updated
ですが、これはパイプラインを直したことによる再変換で、`uploadDate` は
`previousUploadDate` と同じままです。素朴に差分を履歴にすると全規則に
「2026-09-09 更新」が付き、利用者には JAF が更新したように見えてしまいます。
判定は「uploadDate が変わったか」で行い、変わっていないものは
`reconvert` として記録だけして表示対象から外します。

    python pipeline/build_history.py
    python pipeline/build_history.py --print jaf_sport_reg_race
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jafreg.series import edition_label, effective_date, series_key  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  読めません（無視します）: {path.name}: {exc}")
        return default


def build(data_dir: Path = DATA) -> dict[str, Any]:
    state = _load(data_dir / "state.json", {"docs": {}})
    docs_state: dict[str, dict[str, Any]] = state.get("docs") or {}

    # docId → {日付: 種別}。同じ日付を二重に積まないよう dict で持つ。
    observed: dict[str, dict[str, str]] = {}
    reconverts: dict[str, list[str]] = {}
    removed_at: dict[str, str] = {}

    def note(doc_id: str, date: str | None, kind: str) -> None:
        if not date:
            return
        slot = observed.setdefault(doc_id, {})
        # updated は listed より強い（同じ日に両方来たら updated を残す）
        if slot.get(date) != "updated":
            slot[date] = kind

    for path in sorted((data_dir / "changes").glob("*.json")):
        detected = path.stem  # ファイル名がこちらの検出日
        changes = _load(path, {})
        for entry in changes.get("added") or []:
            note(entry.get("docId", ""), entry.get("uploadDate"), "listed")
        for entry in changes.get("updated") or []:
            doc_id = entry.get("docId", "")
            up = entry.get("uploadDate")
            prev = entry.get("previousUploadDate")
            if up and prev and up != prev:
                note(doc_id, prev, "listed")   # 変更前の掲載日も履歴に残す
                note(doc_id, up, "updated")
            else:
                # JAF 側は変わっていない。こちらの再変換なので表示しない。
                note(doc_id, up, "listed")
                reconverts.setdefault(doc_id, []).append(detected)
        for doc_id in changes.get("removed") or []:
            removed_at[doc_id] = detected

    # 現在の掲載日は必ず履歴に含める（差分ファイルが残っていない時期の分）
    for doc_id, rec in docs_state.items():
        note(doc_id, rec.get("uploadDate"), "listed")
        if rec.get("removedAt"):
            removed_at.setdefault(doc_id, str(rec["removedAt"])[:10])

    # --- 系列（年度版）にまとめる -------------------------------------
    series: dict[str, list[dict[str, Any]]] = {}
    for doc_id, rec in docs_state.items():
        if rec.get("removedAt"):
            continue  # 一覧から消えた版は系列の相互リンクには出さない
        title = rec.get("title") or doc_id
        series.setdefault(series_key(doc_id), []).append(
            {
                "docId": doc_id,
                "title": title,
                "edition": edition_label(doc_id, title),
                "effectiveDate": effective_date(doc_id, title),
                "uploadDate": rec.get("uploadDate"),
            }
        )
    for members in series.values():
        members.sort(key=lambda m: (m["effectiveDate"] or "0000-00-00", m["docId"]))

    # --- 文書ごとの履歴 ------------------------------------------------
    out_docs: dict[str, dict[str, Any]] = {}
    for doc_id, rec in docs_state.items():
        key = series_key(doc_id)
        title = rec.get("title") or doc_id
        events: list[dict[str, Any]] = []

        for date, kind in sorted(observed.get(doc_id, {}).items()):
            events.append({"date": date, "type": kind})

        # 同じ系列の「別の版が公開された」も履歴として並べる。
        # これがあると、2026 年版を見ている人が 2027 年版の存在に気づける。
        for other in series.get(key, []):
            if other["docId"] == doc_id or not other.get("uploadDate"):
                continue
            events.append(
                {
                    "date": other["uploadDate"],
                    "type": "edition",
                    "docId": other["docId"],
                    "edition": other["edition"] or other["title"],
                }
            )

        if doc_id in removed_at:
            events.append({"date": removed_at[doc_id], "type": "removed"})

        events.sort(key=lambda e: (e["date"], e["type"]))

        out_docs[doc_id] = {
            "series": key,
            "edition": edition_label(doc_id, title),
            "effectiveDate": effective_date(doc_id, title),
            "events": events,
            # 表示しないが、追跡のために残す
            "reconvertedAt": sorted(set(reconverts.get(doc_id, []))),
        }

    return {
        "generatedAt": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "docs": out_docs,
        # 複数の版を持つ系列だけ残す（単独のものは相互リンクが要らない）
        "series": {k: v for k, v in series.items() if len(v) > 1},
    }


def github_summary(history: dict[str, Any], data_dir: Path, days: int = 14) -> str:
    """CI のジョブサマリ用の markdown。

    週次実行だと誰も画面を見ていないので、「JAF が更新した規則」と
    「新しい年度版が出た規則」だけを短く出す。再変換は出さない。
    """
    today = _dt.date.today()
    cutoff = (today - _dt.timedelta(days=days)).isoformat()
    state = _load(data_dir / "state.json", {"docs": {}}).get("docs") or {}

    def title_of(doc_id: str) -> str:
        return (state.get(doc_id) or {}).get("title") or doc_id

    updates: list[str] = []
    new_editions: list[str] = []
    for doc_id, info in sorted(history["docs"].items()):
        for e in info["events"]:
            if e["date"] < cutoff:
                continue
            if e["type"] == "updated":
                updates.append(f"- {e['date']} {title_of(doc_id)}")
            elif e["type"] == "listed" and info["series"] in history["series"]:
                new_editions.append(
                    f"- {e['date']} {title_of(doc_id)}（{info['edition'] or '新しい版'}）"
                )

    lines = [f"## 更新履歴（直近 {days} 日）", ""]
    lines.append(f"### JAF が更新した規則: {len(updates)} 件")
    lines.extend(sorted(set(updates)) or ["- なし"])
    lines.append("")
    lines.append(f"### 新しい年度版: {len(set(new_editions))} 件")
    lines.extend(sorted(set(new_editions)) or ["- なし"])
    lines.append("")
    lines.append(f"複数の年度版を持つ規則: {len(history['series'])} 件")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="規則ごとの更新履歴を組み立てる")
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--out", default=str(DATA / "history.json"))
    ap.add_argument("--print", dest="show", default=None, help="系列キーの内容を出して終わる")
    ap.add_argument(
        "--github-summary",
        action="store_true",
        help="CI のジョブサマリ用 markdown を標準出力に出す（history.json は書かない）",
    )
    ap.add_argument("--days", type=int, default=14, help="--github-summary で見る日数")
    args = ap.parse_args()

    history = build(Path(args.data))

    if args.github_summary:
        print(github_summary(history, Path(args.data), args.days))
        return 0

    if args.show:
        members = history["series"].get(args.show)
        if not members:
            # 単独の系列でも中身を見たい
            members = [
                {"docId": d, **v}
                for d, v in history["docs"].items()
                if v["series"] == args.show
            ]
        if not members:
            print(f"系列が見つかりません: {args.show}")
            return 1
        for m in members:
            doc_id = m["docId"]
            info = history["docs"].get(doc_id, {})
            print(f"■ {doc_id}  {info.get('edition','')}")
            for e in info.get("events", []):
                extra = f" → {e['edition']}" if e.get("edition") else ""
                print(f"    {e['date']}  {e['type']}{extra}")
        return 0

    Path(args.out).write_text(
        json.dumps(history, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    multi = len(history["series"])
    updated = sum(
        1
        for v in history["docs"].values()
        if any(e["type"] == "updated" for e in v["events"])
    )
    print(
        f"{len(history['docs'])} 文書 / 複数版を持つ系列 {multi} / "
        f"JAF 更新の記録がある文書 {updated} → {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
