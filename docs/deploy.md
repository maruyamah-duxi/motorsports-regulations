# 変更を確認する手順（ローカル → Cloud Run）

## 0. 前提: いま動いている Cloud Run はリポジトリから再現できない

`jp.motorsports-regulations.org` の現行デプロイは **Google AI Studio の「Cloud Run にデプロイ」機能**が作ったものです。
実際に配信されている HTML には AI Studio が注入した以下のラッパーが入っていました。

```js
const geminiUrl = window.location.origin + '/api-proxy';
window.API_KEY = 'UNUSED_PLACEHOLDER_FOR_API_KEY';
```

つまり Gemini の呼び出しは AI Studio 側のプロキシ経由で、**API キーはリポジトリにもバンドルにも入っていません**（現時点で鍵の露出はありません）。
一方でリポジトリには `Dockerfile` も起動サーバもなく、この構成を自分で再現する手段がありませんでした。

そこで今回 `Dockerfile` と `server/` を追加し、**リポジトリから自分でビルド・デプロイできる**ようにしています。

> ⚠️ 自前イメージには AI Studio の `/api-proxy` はありません。
> フロントエンドも AI チャットを載せていません（`/api/ask` の RAG を
> 実装してから、鍵をサーバ側に置いた状態で戻します）。
> 規則の閲覧・全文検索は動きます。

---

## 1. いちばん速い確認: ローカルで静的 HTML を開く

パイプラインの修正（本文抽出・図版・段落）は**静的ファイルを作るだけ**なので、
デプロイ不要で確認できます。

**Python 3.10 以上が必要です。** macOS 標準の 3.9 では `pymupdf` が入りません。

```bash
python3 --version                      # 3.10 未満なら
brew install python@3.12
python3.12 -m venv .venv && source .venv/bin/activate

pip install -r pipeline/requirements.txt

# 手元の PDF を 1 本変換
python pipeline/cli.py convert-one samples/xxx.pdf --title "テスト" --out content

# ブラウザで開く
open content/<docId>/index.html
```

## 2. 検索 API 込みでローカル確認

```bash
pip install -r server/requirements.txt
python pipeline/build_index.py                 # content/ → data/search.db
npm install && npm run build                   # dist/ を作る
uvicorn server.app:app --reload --port 8080
```

フロントエンドを触るときは、上の uvicorn を動かしたまま別のターミナルで
`npm run dev` を立てると、Vite の開発サーバ（:3000）が `/api` と `/content` を
:8080 に転送するのでホットリロードが効きます。

- <http://localhost:8080/> … SPA
- <http://localhost:8080/content/<docId>/index.html> … 規則本文
- <http://localhost:8080/api/search?q=ロールケージ> … 全文検索
- <http://localhost:8080/api/docs> … API の一覧（Swagger UI）
- <http://localhost:8080/healthz> … 各アセットが揃っているか

## 3. コンテナで確認（Cloud Run と同じ形）

```bash
docker build -t jaf-reg .
docker run --rm -p 8080:8080 jaf-reg
```

これが通れば Cloud Run でもほぼそのまま動きます。

### Python を入れずにパイプラインだけ回す

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
  bash -c "pip install -q -r pipeline/requirements.txt && python pipeline/cli.py sync"
```

`content/` と `data/` はカレントディレクトリに出るので、そのままコミットできます。

---

## 4. Cloud Run で確認する

### 4-1. 本番に影響を与えずにプレビューする（推奨）

`--no-traffic --tag` を付けると、**新しいリビジョンを 0% トラフィックでデプロイ**し、
専用の URL でだけ確認できます。既存のリビジョンは 100% のまま動き続けます。

```bash
gcloud config set project gen-lang-client-0036162343

gcloud run deploy jaf-motorsports-regulations-explorer \
  --source . \
  --region us-west1 \
  --no-traffic \
  --tag preview \
  --allow-unauthenticated
```

デプロイ後に表示される
`https://preview---jaf-motorsports-regulations-explorer-XXXXXXXX.us-west1.run.app`
がプレビュー URL です。ここで確認します。

```bash
curl -s https://preview---.../healthz | jq
curl -s 'https://preview---.../api/search?q=安全ベルト&limit=3' | jq '.items[].headingPath'
```

### プレビューで確認すること

| 見るところ | 期待 |
| --- | --- |
| `/healthz` | `searchDb` `content` `dist` がすべて true |
| トップ | 規則 160 件・約 5,148 ページと表示される |
| 検索「ロールケージ 溶接」 | 見出し階層つきでヒットし、抜粋がハイライトされる |
| 検索結果の「該当箇所を開く」 | 該当する条にスクロールする |
| 規則ページを直接リロード | 404 にならない（SPA フォールバックが効いている） |
| 図版の多い規則（第1編レース車両規定など） | 図が本文の正しい位置に出る |
| ダークモード | OS の設定に追随する |

### 4-2. 問題なければトラフィックを切り替える

```bash
gcloud run services update-traffic jaf-motorsports-regulations-explorer \
  --region us-west1 --to-latest
```

### 4-3. 戻したくなったら

```bash
# リビジョン一覧
gcloud run revisions list --service jaf-motorsports-regulations-explorer --region us-west1

# 特定のリビジョンに 100% 戻す
gcloud run services update-traffic jaf-motorsports-regulations-explorer \
  --region us-west1 --to-revisions <REVISION_NAME>=100
```

Cloud Run はリビジョンが残るので、**切り戻しは常に数十秒でできます**。
まずは 4-1 のプレビューだけ回して、納得してから 4-2 に進むのが安全です。

### 4-4. 別サービスとして立てたい場合

現行サービスに一切触れたくなければ、別名でデプロイしても構いません。

```bash
gcloud run deploy jaf-regulations-next \
  --source . --region us-west1 --allow-unauthenticated
```

独自ドメインは現行サービスに向いたままなので、影響はありません。

---

## 5. 初回に必要な API の有効化

`gcloud run deploy --source` は Cloud Build と Artifact Registry を使います。
まだ有効化していなければ一度だけ:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
```

---

## 6. これから自動化する場合

`content/` は GitHub Actions が毎日更新するので、デプロイもそこに繋げられます。

1. Workload Identity 連携で GitHub Actions → GCP の認証を設定
2. `regulations-sync` が `content/` を更新したときだけ `gcloud run deploy` を実行
3. まず `--no-traffic --tag preview` で上げ、確認後に手動で `update-traffic`

規則の内容が変わるサービスなので、**自動でトラフィックまで切り替えない**運用を勧めます。
