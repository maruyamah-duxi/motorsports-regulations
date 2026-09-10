#!/usr/bin/env python3
"""埋め込みキャッシュを GCS に置き、git からは追い出すための出し入れ。

`data/embeddings.sqlite` は 34MB の SQLite で、git の差分圧縮がまったく
効かない。1 行増えるだけでも毎回まるごと新しい blob が積まれるため、
規則が改訂されるたびにリポジトリが 30MB 単位で膨らんでいく。
そこで実体は GCS に置き、git には**マニフェスト（数百バイト）だけ**を
コミットする。

    # 取得した埋め込みを GCS へ上げ、マニフェストを更新する
    python pipeline/embeddings_store.py push --bucket gs://BUCKET

    # デプロイ前に手元へ落とす（マニフェストの sha256 で検証する）
    python pipeline/embeddings_store.py pull

    # 手元・マニフェスト・GCS の食い違いを見る
    python pipeline/embeddings_store.py status

マニフェストが「このリポジトリはベクトルを前提にしている」という宣言に
なっていて、`build_index.py --require-vectors` がそれを見る。これにより
**中身が空の埋め込みで気づかずデプロイしてしまう事故**を防ぐ
（実際に一度、0 行のファイルをコミットしたまま公開した）。

GCS の操作は gcloud CLI に任せる。再開可能アップロードや認証を
自前で書く必要がないため。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = ROOT / "data" / "embeddings.sqlite"
DEFAULT_MANIFEST = ROOT / "data" / "embeddings.manifest.json"
OBJECT_NAME = "embeddings/embeddings.sqlite"

# 取得済みベクトルが前回より大幅に減っていたら、事故を疑って止める。
# （catalog の縮小ガードと同じ考え方）
SHRINK_THRESHOLD = 0.9


# ---------------------------------------------------------------------------
# 小道具
# ---------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def inspect_cache(path: Path) -> dict[str, Any]:
    """埋め込みキャッシュの中身を数える。

    複数のモデル・次元が混ざっていると search.db 側の突き合わせが
    意図しない結果になるので、その場合は内訳を返して呼び出し側で弾く。
    """
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        groups = con.execute(
            "SELECT model, dim, count(*) FROM vectors GROUP BY model, dim ORDER BY 3 DESC"
        ).fetchall()
    finally:
        con.close()
    return {
        "rows": sum(g[2] for g in groups),
        "groups": [{"model": g[0], "dim": g[1], "rows": g[2]} for g in groups],
        "bytes": path.stat().st_size,
    }


def read_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_uri(args: argparse.Namespace, manifest: dict[str, Any] | None) -> str | None:
    """GCS の宛先を決める。--bucket → 環境変数 → マニフェストの順。"""
    if args.bucket:
        bucket = args.bucket
    else:
        bucket = os.environ.get("EMBEDDINGS_BUCKET", "")
    if bucket:
        bucket = bucket.rstrip("/")
        if not bucket.startswith("gs://"):
            bucket = f"gs://{bucket}"
        return f"{bucket}/{OBJECT_NAME}"
    if manifest and manifest.get("uri"):
        return str(manifest["uri"])
    return None


def gcloud(*args: str) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("gcloud")
    if not exe:
        sys.exit(
            "gcloud が見つかりません。Google Cloud SDK を入れて `gcloud auth login` を"
            "済ませてください（https://cloud.google.com/sdk/docs/install）。"
        )
    proc = subprocess.run([exe, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        sys.exit(f"gcloud {' '.join(args)} が失敗しました:\n{detail}")
    return proc


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


def cmd_push(args: argparse.Namespace) -> int:
    cache = Path(args.cache)
    manifest_path = Path(args.manifest)
    if not cache.exists():
        print(f"埋め込みキャッシュがありません: {cache}")
        print("先に `python pipeline/build_embeddings.py` を実行してください。")
        return 1

    info = inspect_cache(cache)
    if info["rows"] == 0:
        print(f"{cache} は 0 行です。空のファイルは上げません。")
        print("`python pipeline/build_embeddings.py` を実行してから push してください。")
        return 1
    if len(info["groups"]) > 1 and not args.force:
        print("モデル・次元が混ざっています。--force で上書きできますが、意図を確認してください:")
        for g in info["groups"]:
            print(f"  {g['model']} / {g['dim']} 次元 … {g['rows']} 行")
        return 1

    previous = read_manifest(manifest_path)
    if previous and not args.force:
        before = int(previous.get("rows") or 0)
        if before and info["rows"] < before * SHRINK_THRESHOLD:
            print(
                f"ベクトルが {before} 行 → {info['rows']} 行 に減っています。"
                "取り違えを疑って中止しました（--force で強行できます）。"
            )
            return 1

    uri = resolve_uri(args, previous)
    if not uri:
        print("GCS の宛先が分かりません。--bucket gs://BUCKET を指定してください。")
        return 1

    digest = sha256_of(cache)
    if previous and previous.get("sha256") == digest and not args.force:
        print(f"GCS 上のものと同一です（{info['rows']} 行）。何もしません。")
        return 0

    print(f"{cache.name} ({info['bytes']/1024/1024:.1f} MB / {info['rows']} 行) → {uri}")
    gcloud("storage", "cp", str(cache), uri)

    top = info["groups"][0]
    write_manifest(
        manifest_path,
        {
            "uri": uri,
            "model": top["model"],
            "dim": top["dim"],
            "rows": info["rows"],
            "bytes": info["bytes"],
            "sha256": digest,
            "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    print(f"マニフェストを更新しました: {manifest_path}")
    print("このマニフェストを git にコミットしてください（実体はコミットされません）。")
    return 0


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------


def cmd_pull(args: argparse.Namespace) -> int:
    cache = Path(args.cache)
    manifest = read_manifest(Path(args.manifest))
    uri = resolve_uri(args, manifest)

    if manifest is None and not args.latest:
        print(f"マニフェストがありません: {args.manifest}")
        print("初回は `embeddings_store.py push --bucket gs://BUCKET` で作ってください。")
        print("バケットの最新をそのまま取るだけなら --latest を付けてください。")
        return 1
    if not uri:
        print("GCS の取得元が分かりません。--bucket gs://BUCKET を指定してください。")
        return 1

    expected = (manifest or {}).get("sha256") if not args.latest else None
    if expected and cache.exists() and sha256_of(cache) == expected:
        print(f"すでに最新です（{manifest.get('rows')} 行）。取得しません。")
        return 0

    cache.parent.mkdir(parents=True, exist_ok=True)
    # 途中で失敗しても既存のファイルを壊さないよう、別名で落としてから入れ替える。
    tmp_dir = Path(tempfile.mkdtemp(dir=cache.parent, prefix=".pull-"))
    tmp = tmp_dir / cache.name
    try:
        print(f"{uri} → {cache}")
        gcloud("storage", "cp", uri, str(tmp))

        info = inspect_cache(tmp)
        if info["rows"] == 0:
            print("取得したファイルが 0 行です。GCS 上のものが壊れている可能性があります。")
            return 1

        if expected:
            digest = sha256_of(tmp)
            if digest != expected:
                print("マニフェストの sha256 と一致しません。")
                print(f"  マニフェスト: {expected[:16]}… / {manifest.get('rows')} 行")
                print(f"  GCS 上       : {digest[:16]}… / {info['rows']} 行")
                print("git pull でマニフェストを更新するか、--latest で最新を受け入れてください。")
                return 1

        tmp.replace(cache)
        print(f"{info['rows']} 行 / {info['bytes']/1024/1024:.1f} MB を配置しました。")
        return 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    cache = Path(args.cache)
    manifest = read_manifest(Path(args.manifest))

    print("[手元]")
    if cache.exists():
        info = inspect_cache(cache)
        print(f"  {cache} … {info['rows']} 行 / {info['bytes']/1024/1024:.1f} MB")
        for g in info["groups"]:
            print(f"    {g['model']} / {g['dim']} 次元 … {g['rows']} 行")
        local_sha: str | None = sha256_of(cache)
        print(f"    sha256 {local_sha[:16]}…")
    else:
        local_sha = None
        print(f"  {cache} … ありません（embeddings_store.py pull で取得）")

    print("[マニフェスト]")
    if manifest:
        print(f"  {manifest['uri']}")
        print(
            f"  {manifest.get('rows')} 行 / {manifest.get('model')} / "
            f"{manifest.get('dim')} 次元 / {manifest.get('updatedAt')}"
        )
        print(f"  sha256 {str(manifest.get('sha256'))[:16]}…")
        if local_sha:
            same = local_sha == manifest.get("sha256")
            print(f"  手元と一致: {'はい' if same else 'いいえ（pull が必要）'}")
    else:
        print(f"  {args.manifest} … ありません")

    uri = resolve_uri(args, manifest)
    print("[GCS]")
    if uri and not args.offline:
        proc = gcloud("storage", "ls", "-l", uri)
        for line in proc.stdout.strip().splitlines():
            print(f"  {line.strip()}")
    elif uri:
        print(f"  {uri}（--offline のため参照しません）")
    else:
        print("  宛先不明")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="埋め込みキャッシュを GCS と出し入れする")
    ap.add_argument("--cache", default=str(DEFAULT_CACHE))
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument(
        "--bucket",
        default="",
        help="gs://BUCKET（省略時は環境変数 EMBEDDINGS_BUCKET、次にマニフェストの uri）",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("push", help="手元の埋め込みを GCS へ上げ、マニフェストを更新する")
    p.add_argument("--force", action="store_true", help="縮小ガードと混在チェックを無視する")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("pull", help="GCS から手元へ取得する（デプロイ前に実行）")
    p.add_argument(
        "--latest",
        action="store_true",
        help="マニフェストの sha256 を検証せず、バケットの最新を受け入れる",
    )
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("status", help="手元・マニフェスト・GCS の食い違いを見る")
    p.add_argument("--offline", action="store_true", help="GCS を参照しない")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
