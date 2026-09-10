# pipeline — JAF 諸規則 PDF → 構造化 HTML

設計の背景と方針は [`docs/architecture.md`](../docs/architecture.md)、
デプロイと動作確認は [`docs/deploy.md`](../docs/deploy.md) を参照。

## セットアップ

**Python 3.10 以上が必要です。** macOS の標準 `python3` は 3.9 系のことが多く、
その場合 `pymupdf==1.28.2` が入らず次のエラーになります。

```
ERROR: Could not find a version that satisfies the requirement pymupdf==1.28.2
ERROR: Ignored the following versions that require a different python version:
       ... Requires-Python >=3.10
```

### A. Python 3.12 を入れる（おすすめ）

```bash
python3 --version          # 3.10 未満ならこちら
brew install python@3.12
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r pipeline/requirements.txt
```

以降は `source .venv/bin/activate` した状態で `python pipeline/cli.py ...` を実行します。

### B. Docker で動かす（Python を入れたくない場合）

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
  bash -c "pip install -q -r pipeline/requirements.txt && python pipeline/cli.py sync"
```

生成物（`content/` `data/`）はカレントディレクトリにそのまま出ます。

### OCR フォールバックを使う場合のみ

```bash
# macOS
brew install tesseract tesseract-lang
# Linux
sudo apt install tesseract-ocr tesseract-ocr-jpn tesseract-ocr-jpn-vert
```

## 使い方

```bash
# JAF の諸規則ページを巡回してカタログだけ作る
python pipeline/cli.py crawl

# 巡回 → 差分検出 → 変更分だけ変換（本番と同じ動作）
python pipeline/cli.py sync

# 先頭 3 件だけで動作確認
python pipeline/cli.py sync --limit 3

# 特定の 1 本だけ再変換
python pipeline/cli.py sync --only <docId> --force

# 手元の PDF を 1 本だけ変換（JAF に繋がらない環境での検証用）
python pipeline/cli.py convert-one samples/rule.pdf --title "国内競技規則" --out content

# 変換せず構造だけ診断（図版領域・表・フォント・文字数）
python pipeline/cli.py probe samples/rule.pdf --out probe.json --pages 12

# 公示一覧を巡回（諸規則一覧より約1か月早く更新が出る／対比表PDFを拾う）
python pipeline/cli.py announcements
python pipeline/cli.py announcements --since 2025-01-01   # 期間を絞る

# content/ から全文検索用の SQLite を作る
python pipeline/build_index.py

# 更新履歴（年度版の系列も含む）を組み立てる
python pipeline/build_history.py
python pipeline/build_history.py --print jaf_sport_reg_race   # 1 系列の中身を見る

# 条単位の改正差分（前年度版と、退避してある前の版が相手）
python pipeline/build_diffs.py
python pipeline/build_diffs.py --print <docId>   # 1 文書の差分を並べて見る

# 注意書きやスタイルだけを直したとき（PDF は読み直さない・数秒）
python pipeline/rerender.py --dry-run
python pipeline/rerender.py

# 埋め込み（RAG 用ベクトル）。実体は GCS にあり git には入らない
python pipeline/embeddings_store.py pull    # デプロイ前に手元へ落とす
python pipeline/build_embeddings.py         # 本文が変わった分だけ取得
python pipeline/embeddings_store.py push    # GCS とマニフェストを更新
```

埋め込みの置き場と検証の仕組みは
[`../docs/embeddings-storage.md`](../docs/embeddings-storage.md) を参照してください。

主なオプション: `--dpi`（図版の解像度, 既定 200）, `--ocr`（テキスト層の無いページを OCR）,
`--delay`（リクエスト間隔, 既定 1.5 秒）。

## 出力

```
data/catalog.json              JAF の配布一覧
data/state.json                docId → sha256 / uploadDate / 変換統計
data/index.json                フロント用の軽量インデックス
data/changes/YYYY-MM-DD.json   その日の差分
data/search.db                 全文検索用 SQLite（build_index.py が生成、git 管理外）
data/embeddings.sqlite         埋め込みキャッシュ（実体は GCS、git 管理外）
data/embeddings.manifest.json  上の実体を指すマニフェスト（git 管理）
data/history.json              規則ごとの更新履歴と年度版の系列
content/<docId>/document.json  構造化本文
content/<docId>/index.html     単体で読める HTML
content/<docId>/assets/*.webp  図版
content/<docId>/previous.json  上書き前の版（改正差分の比較元）
content/<docId>/diff-*.json    条単位の改正差分
data/diffs.json                差分の一覧
data/announcements.json        JAF の公示（規則変更・対比表PDFへのリンク）
```

原本 PDF は `.cache/pdf/` に置かれ、git には入りません（JAF の著作物のため）。

## モジュール

| ファイル | 役割 |
| --- | --- |
| `jafreg/catalog.py` | 諸規則ページの DOM からカタログを抽出 |
| `jafreg/announcements.py` | 公示一覧（JSON API）と詳細ページの解析 |
| `jafreg/fetch.py` | 礼儀正しい HTTP 取得と SHA-256 |
| `jafreg/layout.py` | ページ解析。XY-Cut / 図版領域 / 見出し / 段落再構成 / 柱の除去 |
| `jafreg/convert.py` | PDF 1 本 → document.json + 図版 + HTML |
| `jafreg/render.py` | 構造化データ → HTML（出典の注意書きもここ） |
| `rerender.py` | document.json → index.html の作り直し（PyMuPDF 不要） |
| `jafreg/series.py` | 年度版をまとめる系列キー（PDF のファイル名から取る） |
| `build_history.py` | 更新履歴の組み立て。JAF の更新と自分の再変換を区別する |
| `jafreg/clausediff.py` | 条単位の突き合わせと差分 |
| `build_diffs.py` | 改正差分の算出。前年度版と前の版の両方を相手にする |
| `build_index.py` | content/ → SQLite FTS5 (trigram) 全文検索 DB。`--require-vectors` でベクトル不足を検出 |
| `build_embeddings.py` | チャンク本文 → 埋め込みベクトル（本文ハッシュでキャッシュ） |
| `embeddings_store.py` | 埋め込みキャッシュを GCS と出し入れ（`push` / `pull` / `status`） |
| `commit_message.py` | CI 用。差分レポートからコミットメッセージを組み立てる |
