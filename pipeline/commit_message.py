#!/usr/bin/env python3
"""直近の差分レポートからコミットメッセージを組み立てる（CI 用）."""

from __future__ import annotations

import glob
import json

files = sorted(glob.glob("data/changes/*.json"))
if not files:
    print("content: JAF 諸規則を更新")
    raise SystemExit(0)

d = json.load(open(files[-1], encoding="utf-8"))
added = d.get("added", [])
updated = d.get("updated", [])
removed = d.get("removed", [])
failed = d.get("failed", [])

head = f"content: 追加{len(added)}件 / 更新{len(updated)}件"
if removed:
    head += f" / 削除{len(removed)}件"
if failed:
    head += f" / 失敗{len(failed)}件"

lines = [head, ""]
for x in (added + updated)[:20]:
    prev = x.get("previousUploadDate")
    when = x.get("uploadDate") or "?"
    lines.append(f"- {x['title']}（{prev} → {when}）" if prev else f"- {x['title']}（{when}）")
for f in failed[:10]:
    lines.append(f"- [失敗/{f.get('stage')}] {f.get('docId')}: {str(f.get('error'))[:120]}")

print("\n".join(lines).rstrip())
