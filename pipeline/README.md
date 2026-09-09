# pipeline — JAF 諸規則 PDF → 構造化 HTML

設計の背景と方針は [`docs/architecture.md`](../docs/architecture.md) を参照。

## セットアップ

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r pipeline/requirements.txt
# OCR フォールバックを使う場合のみ
#   macOS: brew install tesseract tesseract-lang
#   Linux: apt install tesseract-ocr tesseract-ocr-jpn tesseract-ocr-jpn-vert
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
python pipeline/cli.py sync --only 2025-jaf-kokunai-sporting-code-20250101-xxxxxx --force

# 手元の PDF を 1 本だけ変換（JAF に繋がらない環境での検証用）
python pipeline/cli.py convert-one path/to/rule.pdf --title "国内競技規則" --out /tmp/out

# 変換せず構造だけ診断（図版領域・表・フォント・文字数）
python pipeline/cli.py probe path/to/rule.pdf --out probe.json --pages 12
```

主なオプション: `--dpi`（図版の解像度, 既定 200）, `--ocr`（テキスト層の無いページを OCR）, `--delay`（リクエスト間隔, 既定 1.5 秒）。

## 出力

```
data/catalog.json              JAF の配布一覧
data/state.json                docId → sha256 / uploadDate / 変換統計
data/index.json                フロント用の軽量インデックス
data/changes/YYYY-MM-DD.json   その日の差分
content/<docId>/document.json  構造化本文
content/<docId>/index.html     単体で読める HTML
content/<docId>/assets/*.webp  図版
```

原本 PDF は `.cache/pdf/` に置かれ、git には入りません（JAF の著作物のため）。

## モジュール

| ファイル | 役割 |
| --- | --- |
| `jafreg/catalog.py` | 諸規則ページの DOM からカタログを抽出 |
| `jafreg/fetch.py` | 礼儀正しい HTTP 取得と SHA-256 |
| `jafreg/layout.py` | ページ解析。XY-Cut / 図版領域 / 見出し / ヘッダ除去 |
| `jafreg/convert.py` | PDF 1 本 → document.json + 図版 + HTML |
| `jafreg/render.py` | 構造化データ → HTML |
