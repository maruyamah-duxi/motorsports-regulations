# 変更を確認する手順（ローカル → Cloud Run）

## 0. 重要: 既存サービスに上書きデプロイしてはいけない

2026-09-09、既存サービス `jaf-motorsports-regulations-explorer` に
`--source .` でプレビューを上げたところ、**API は新しいのにフロントエンドは
古いまま**という状態になりました。プレビュー URL で実際に観測した挙動:

| パス | 返ってきたもの |
| --- | --- |
| `/api/documents` | 新しい FastAPI（160 件） |
| `/api/search` | 新しい FastAPI（`staticUrl` あり＝最新コード） |
| `/content/...` | 新しい FastAPI（404 は JSON） |
| `/` `/doc/abc` `/api-proxy` `/assets/*` | **同一の 99,232 バイトの HTML**（Google Sans と `@modelcontextprotocol/sdk` を読み込む AI Studio のシェル。中身は旧アプリ） |
| `/healthz` | Google の 404 ページ（※これは別要因。下の注記を参照） |

`/` `/doc/*` `/assets/*` が**まったく同じ HTML**を返し、そこに AI Studio が
注入したシェルが入っていることから、このサービスには **AI Studio が作った
ラッパーが前段に残っています**。自前のコンテナのフロントエンドは配られません。

**対処: 新しいサービスとしてデプロイしてください**（下の 4-4）。
AI Studio が触っていないサービスなら、この問題は起きません。
動作を確認してから独自ドメインを付け替えるのが安全です。

> **注記: `/healthz` は使えません。**
> Cloud Run（Google Front End）は `/healthz` ちょうどのパスを横取りし、
> アプリに届く前に自前の 404 を返します。実測:
> `/healthz` → Google の 404 HTML、`/healthz/` `/healthz2` `/api/healthz` → アプリに到達。
> 動作確認には **`/api/healthz`** を使ってください。

現状を確認したい場合:

```bash
gcloud run services describe jaf-motorsports-regulations-explorer \
  --region us-west1 --format=yaml | head -80
gcloud run revisions list --service jaf-motorsports-regulations-explorer --region us-west1
```

---

## 0-b. 前提: いま動いている Cloud Run はリポジトリから再現できない

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
- <http://localhost:8080/api/healthz> … 各アセットが揃っているか
  （`/healthz` はローカルでは使えますが、Cloud Run では Google 側に横取りされます）

## 3. コンテナで確認（Cloud Run と同じ形）

**先に埋め込みを手元へ落としてください。** 実体は git ではなく GCS にあります
（[`embeddings-storage.md`](./embeddings-storage.md)）。落とし忘れると
`--require-vectors` のガードでビルドが失敗します。

```bash
python pipeline/embeddings_store.py pull

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
curl -s https://preview---.../api/healthz | jq
curl -s 'https://preview---.../api/search?q=安全ベルト&limit=3' | jq '.items[].headingPath'
```

### プレビューで確認すること

**まず `/api/healthz` の `docsWith` を見ること。** ここが 0 のものは
`.dockerignore` / `.gcloudignore` からファイルが漏れています。一度
`data/announcement_links.json` を `data/announcements.json` と書き間違えて、
公示が黙って 0 件のまま公開してしまいました。

```bash
curl -s $D/api/healthz | python3 -m json.tool
# "docsWith": {"history": 160, "diffs": 5, "announcements": 60}
```

`build_index.py` はビルドログにも入力の有無を出します。Cloud Build のログで
「見つかりません」が出ていないか確認してください。

| 見るところ | 期待 |
| --- | --- |
| `/api/healthz` | `searchDb` `content` `dist` が true。`docsWith` がすべて 0 でない |
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

### 4-4. 別サービスとして立てる（いまはこちらを使う）

上の 0 のとおり、既存サービスには AI Studio のラッパーが残っています。
**新しいサービス名でデプロイしてください。**

```bash
python pipeline/embeddings_store.py pull    # 埋め込みの実体は GCS にある

gcloud run deploy jaf-regulations-next \
  --source . --region us-west1 --allow-unauthenticated
```

独自ドメインは現行サービスに向いたままなので、公開中のサイトには影響しません。
新サービスで確認が取れたら、ドメインマッピングを付け替えます。

```bash
# 確認
curl -s https://jaf-regulations-next-XXXX.us-west1.run.app/api/healthz | jq

# ドメインを付け替える（確認が取れてから）
gcloud beta run domain-mappings create \
  --service jaf-regulations-next --domain jp.motorsports-regulations.org --region us-west1
```

---

## 4-5. Cloud Build トリガーで出す（推奨。アップロード 0）

`gcloud run deploy --source .` は**毎回 121MB をアップロード**します
（`content/` 86MB + `data/embeddings.sqlite` 34MB）。Cloud Build を GitHub
リポジトリに紐づけると Cloud Build 側が直接クローンするので、**アップロードが
0 になります**。配信経路は一切変わらないのでリスクもありません。

副産物として、**埋め込みの取得もビルドがやります**。手元で
`embeddings_store.py pull` を忘れる余地が無くなります。

### 初回だけ: 権限とリポジトリ接続

```bash
gcloud config set project gen-lang-client-0036162343
gcloud services enable cloudbuild.googleapis.com artifactregistry.googleapis.com

**権限を与える相手を推測しないこと。** 新しい Cloud Build のトリガーは
`@cloudbuild.gserviceaccount.com` ではなく **compute のサービスアカウント**で
走ります（2026-09-10 に実測。最初 cloudbuild 側に付けて外しました）。
一度トリガーを実行して、実行主体を実物から確かめるのが確実です。

```bash
# 1) トリガーを 1 回実行し、実行主体を確かめる
gcloud builds triggers run jaf-regulations-deploy --branch=main --region=global \
  --format='value(metadata.build.serviceAccount)'
# → projects/.../serviceAccounts/NNNNN-compute@developer.gserviceaccount.com

# 2) その SA に権限を与える（上で出た方を CB に入れる）
PROJECT_NUMBER=$(gcloud projects describe gen-lang-client-0036162343 --format='value(projectNumber)')
CB="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

# Cloud Run へデプロイする権限
gcloud projects add-iam-policy-binding gen-lang-client-0036162343 \
  --member="serviceAccount:${CB}" --role=roles/run.developer
# Cloud Run サービスの実行 SA を使う権限（ビルド SA と同一なら自分自身に付ける）
gcloud iam service-accounts add-iam-policy-binding "${CB}" \
  --member="serviceAccount:${CB}" --role=roles/iam.serviceAccountUser
# 埋め込みを GCS から読む権限（このバケットに限る）
gcloud storage buckets add-iam-policy-binding \
  gs://gen-lang-client-0036162343-jaf-data \
  --member="serviceAccount:${CB}" --role=roles/storage.objectViewer

# 確認
gcloud projects get-iam-policy gen-lang-client-0036162343 \
  --flatten='bindings[].members' --format='value(bindings.role)' \
  --filter="bindings.members:${CB}"
```

> compute SA は既定で Editor を持っていることが多く、その場合は上の付与なしで
> 通ります。Editor を外している環境では権限エラーになるので、そのときだけ
> 実行してください。

GitHub との接続とトリガーの作成は**コンソールが確実**です
（GitHub App のインストール同意が必要なため）。

1. Cloud Build → トリガー → 「リポジトリを接続」→ GitHub →
   `maruyamah-duxi/motorsports-regulations`
2. トリガーを作成
   - イベント: **手動起動**（push では起動しない）
   - 構成: **Cloud Build 構成ファイル** `/cloudbuild.yaml`
   - 名前: `jaf-regulations-deploy`

**イベントは必ず「手動起動」にしてください。** 規則の内容が変わるサービスなので、
公開は人が判断する方針です（`architecture.md` 7-d）。

### ふだんのデプロイ

```bash
git push        # 先にこれ。Cloud Build は GitHub から取るので push 必須

gcloud builds triggers run jaf-regulations-deploy --branch=main --region=global
```

コンソールのトリガー一覧から「実行」を押しても同じです。ビルドの最後に
`/api/healthz` を叩いて **`docsWith` が 0 のものが無いか確かめてから終わります**。
ここで落ちたら、その回のデプロイは中身が欠けています。

> **`git push` を忘れると古いコードがデプロイされます。** Cloud Build は
> 手元の作業ツリーではなく GitHub を見ます。`--source .` とはここが逆なので
> 注意してください。

### `--source .` はどこで残るか

手元だけの変更を試したいとき（push したくないとき）は従来どおり使えます。
そのときは埋め込みの取得を自分でやってください。

```bash
python pipeline/embeddings_store.py pull
gcloud run deploy jaf-regulations-next --source . --region us-west1 \
  --allow-unauthenticated --memory 1Gi --max-instances 3
```

---

## 5. 初回に必要な API の有効化

`gcloud run deploy --source` は Cloud Build と Artifact Registry を使います。
まだ有効化していなければ一度だけ:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
```

---

## 6. デプロイの自動化はしない

規則の内容が変わるサービスなので、**公開は人が判断する**方針です。
週次同期が更新をコミットしたら GitHub Issue で通知が来るので、それを見て
上の 4-5 のトリガーを手で実行します。

将来もし自動化するなら、`--no-traffic --tag preview` で上げて確認後に
`update-traffic` する 2 段にしてください。**トラフィックの切り替えまで
自動にしない**のが要点です。

---

## 6-b. 「デプロイしたのに古いサイトが出る」

**まず HTML のキャッシュを疑ってください。** 2026-09-11 に踏みました。

検索エンジン向けの HTML 差し込みを入れたとき、応答に
`Cache-Control: public, max-age=300` を付けていました。HTML には
**ビルドごとに変わるアセットのファイル名**（`/assets/index-xxxxxxxx.js`）が
書かれているので、HTML を寝かせると古いアセット名を指したままになり、
ブラウザは古い JS を再利用します。つまり**サーバは新しいのに画面は古い**。

いまは HTML は `no-cache`（「使うな」ではなく「毎回確かめろ」。中身が同じなら
304 で済む）、`/assets/` の中身はファイル名にハッシュが入っているので
`max-age=31536000, immutable` にしています（`server/app.py` の
`HTML_CACHE` / `ASSET_CACHE`）。

### 切り分け

```bash
site=https://jp.motorsports-regulations.org

# 1. サーバ側が新しいか（ここが新しければデプロイは成功している）
curl -sS $site/api/healthz | python3 -m json.tool
curl -sS $site/robots.txt

# 2. HTML が寝ていないか
curl -sSI $site/ | grep -i cache-control        # no-cache であること

# 3. 画面が指しているアセットと、実際に配られているものが一致するか
curl -sS $site/ | grep -o '/assets/index-[^"]*'
```

ブラウザ側はスーパーリロード（Mac は ⌘⇧R）かプライベートウィンドウで
確かめるのが早いです。

### 確認のとき詰まる点

**検索露出を止めている間、`robots.txt` が全面 Disallow なので、
robots.txt に従うツールからはサイトを読めません。** Claude の WebFetch も
含みます。この状態での動作確認は `curl`（robots.txt を見ない）で行って
ください。Claude の実行環境と連携先の Linux VM はどちらも公開サイトに
到達できないので、結局**丸さんの手元の curl 頼み**になります。

---

## 7. 検索エンジンへの登録

> **いまは検索露出を止めてあります（2026-09-10）。** JAF のサイトポリシーに
> 資料の再配布を認めない旨の明示があることが分かったためです
> （[`architecture.md`](./architecture.md) の 9 章）。この節は
> **再開するときの手順**として残しています。まず下の 7-1 で現状を
> 確かめてください。

配信側の準備（`sitemap.xml` / `canonical` / 本文プリレンダ）はコードに入って
います。露出のオン／オフは **`server/seo.py` の `SEARCH_INDEXING` 1 か所**で、
再開は `True` に戻して出すだけです。設計は
[`architecture.md`](./architecture.md) の 7-e。

### 7-1. いまどちらの状態か確認する

```bash
site=https://jp.motorsports-regulations.org

curl -sS $site/robots.txt
curl -sS $site/api/healthz | python3 -m json.tool     # seo.indexing を見る
```

**停止中（現在）なら**こうなります。

```
User-agent: *
Disallow: /
```
```json
"seo": {"origin": "…", "sitemapUrls": 166, "indexing": false}
```

停止中は全応答に `noindex` が付きます。`/content/…` と図版にも掛かります。

```bash
curl -sSI $site/doc/$(curl -sS $site/api/documents \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['items'][0]['docId'])") \
  | grep -i x-robots-tag          # noindex, nofollow
```

**再開後は** `robots.txt` に `Sitemap:` 行が出て、ページから `noindex` が
消えます。中身の作り（`canonical` / `description` / 本文プリレンダ）は
どちらの状態でも入っています。

```bash
doc=$(curl -sS $site/api/documents | python3 -c "import json,sys;print(json.load(sys.stdin)['items'][0]['docId'])")
curl -sS "$site/doc/$doc" | grep -o '<link rel="canonical"[^>]*>'
curl -sS "$site/doc/$doc" | grep -c 'id="prerender"'  # 1
curl -sS $site/sitemap.xml | grep -c '<loc>'          # 166 前後
```

`sitemapUrls` が 1 なら `docs` テーブルを読めていません。Cloud Build の
「中身を確認」ステップは `robots.txt` とページの `<meta>` を突き合わせるので、
どちらの状態でも中途半端なら落ちます。手で出したときは自分で見てください。

### 7-1b. 再開するとき

```bash
# server/seo.py の SEARCH_INDEXING を True にしてから
git commit -m "..." server/seo.py && git push
gcloud builds triggers run jaf-regulations-deploy --branch=main --region=global
```

再開の前に [`architecture.md`](./architecture.md) の 9 章（JAF のサイト
ポリシー）を読んでください。露出を戻すことは、あの記載との食い違いを
広げる方向の判断です。

### 7-2. Google Search Console

1. https://search.google.com/search-console でプロパティを追加。
   **URL プレフィックス**で `https://jp.motorsports-regulations.org/` を入力。
2. 所有権の確認は **HTML タグ**が一番手軽です。渡された
   `<meta name="google-site-verification" content="…">` を `index.html` の
   `<head>` に足してデプロイし、確認ボタンを押します。
   （DNS の TXT レコードでも可。ドメインを触れるならそちらが恒久的です。）
3. 確認できたら **サイトマップ** → `sitemap.xml` を送信。
4. **URL 検査**にトップと規則 1 件を入れて「インデックス登録をリクエスト」。
   全件は待てばよいので、この 2 つだけで足ります。

### 7-3. 数週間後に見るところ

- **ページ** レポート → インデックス済みの件数。160 に近づいていくのが正常。
- 同レポートの「**代替ページ（適切な canonical タグあり）**」に
  `/content/…` が入っていれば、2 系統の URL の寄せが効いています。
- **検索結果のパフォーマンス** → クエリ。条番号や規則名で入ってくるように
  なったかどうか。`description` に条見出しを並べているので、
  どの語で拾われているかがそのまま設計の答え合わせになります。

### 注意

`SEARCH_INDEXING` を触ると**黙って検索から消える／出る**ので、変更したら
必ず上の 7-1 で実物を確認してください。`robots.txt` を手で書き換えるのでは
なく、このスイッチ 1 か所で操作します（ページの `noindex` と `X-Robots-Tag`
まで一緒に切り替わります）。
