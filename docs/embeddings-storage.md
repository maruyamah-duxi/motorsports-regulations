# 埋め込みキャッシュを GCS に置く

`data/embeddings.sqlite`（34MB）を git から追い出し、GCS に置くための手順です。
git に残すのは**マニフェスト（数百バイト）だけ**にします。

## なぜ移すのか

SQLite のバイナリは git の差分圧縮がまったく効きません。1 行増えるだけでも
**毎回 34MB の新しい blob** が履歴に積まれます。JAF の改訂が年 20 回あれば
年 700MB です。図版と違って配信経路に関わらないファイルなので、
いちばん低リスクで効く移設先になります。

| | 移設前 | 移設後 |
| --- | --- | --- |
| git に入るもの | 34MB のバイナリ（改訂ごとに増える） | 約 300 バイトの JSON |
| デプロイ手順 | そのまま | 前に `pull` を 1 回挟む |
| 配信経路 | ― | 変わらない（Cloud Run のイメージに焼き込む） |
| 月額 | ― | 1 円未満（下の「費用」参照） |

## 初回だけ: バケットを作る

```bash
gcloud config set project gen-lang-client-0036162343

# 0) API を有効化する（Secret Manager で忘れて詰まった手順と同じ）
gcloud services enable storage.googleapis.com

# 1) Cloud Run と同じ us-west1 に置く。公開しない。
gcloud storage buckets create gs://gen-lang-client-0036162343-jaf-data \
  --location=us-west1 \
  --uniform-bucket-level-access \
  --public-access-prevention

# 2) 取り違え・巻き戻しに備えて世代管理を有効にする
gcloud storage buckets update gs://gen-lang-client-0036162343-jaf-data --versioning

# 古い世代は 90 日で消す（放っておくと積もるため）
cat > /tmp/lifecycle.json <<'JSON'
{"rule":[{"action":{"type":"Delete"},
          "condition":{"daysSinceNoncurrentTime":90,"isLive":false}}]}
JSON
gcloud storage buckets update gs://gen-lang-client-0036162343-jaf-data \
  --lifecycle-file=/tmp/lifecycle.json
```

設定を確認します。**`gcloud storage` の `--format` はスネークケース**です
（`gcloud storage buckets describe` は JSON API の
`iamConfiguration.publicAccessPrevention` では引けず、黙って空欄になります）。

```bash
gcloud storage buckets describe gs://gen-lang-client-0036162343-jaf-data \
  --format='default(location,public_access_prevention,uniform_bucket_level_access,versioning)'
# location:                     US-WEST1
# public_access_prevention:     enforced        ← ここが inherited なら公開されうる
# uniform_bucket_level_access:  True
# versioning:                   True
```

バケット名は全世界で一意なので、取られていたら別名にしてください
（マニフェストに URI が入るので、名前は後から変えても追随します）。

## 初回だけ: 実体を上げてマニフェストを作る

```bash
python pipeline/embeddings_store.py push \
  --bucket gs://gen-lang-client-0036162343-jaf-data
```

`data/embeddings.manifest.json` が生成されます。**これを git にコミットします。**

```json
{
  "uri": "gs://gen-lang-client-0036162343-jaf-data/embeddings/embeddings.sqlite",
  "model": "gemini-embedding-001",
  "dim": 768,
  "rows": 8494,
  "bytes": 35876864,
  "sha256": "…",
  "updatedAt": "2026-09-10T03:00:00Z"
}
```

そのうえで実体を git の追跡から外します。

```bash
git rm --cached data/embeddings.sqlite
echo "data/embeddings.sqlite" >> .gitignore
git add .gitignore data/embeddings.manifest.json
git commit -m "data: 埋め込みの実体を GCS へ移し、git にはマニフェストだけ残す"
```

> 既に履歴に入っている 34MB は、これでは消えません（消すには履歴の書き換えと
> force push が必要です）。1 個だけなら実害は小さいので、**これ以上増やさない**
> ことを目的にしています。

## ふだんの流れ

### デプロイするとき

```bash
python pipeline/embeddings_store.py pull     # マニフェストの sha256 で検証して取得
gcloud run deploy jaf-regulations-next \
  --source . --region us-west1 --allow-unauthenticated \
  --memory 1Gi --max-instances 3
```

`pull` は手元のファイルがマニフェストと一致していれば何もしません。
`.gcloudignore` は `data/embeddings.sqlite` と
`data/embeddings.manifest.json` だけを通すので、送るものは変わりません。

### 規則が改訂されて本文が変わったとき

```bash
export GEMINI_API_KEY=...
python pipeline/build_index.py            # まず search.db を作り直す
python pipeline/build_embeddings.py       # 変わったチャンクだけ取得
python pipeline/embeddings_store.py push  # GCS とマニフェストを更新
git add data/embeddings.manifest.json && git commit -m "data: 埋め込みを更新"
```

### 食い違いを見る

```bash
python pipeline/embeddings_store.py status
```

手元・マニフェスト・GCS の 3 つを並べて出します。

## 取り違えを止める仕掛け

一度、**中身が 0 行の `embeddings.sqlite` をコミットしたまま公開**しています
（`gcloud run deploy --source .` は git ではなく作業ツリーを送るので、
手元では動いていて気づけませんでした）。同じことを繰り返さないよう、
3 か所で止めます。

| 仕掛け | 止めるもの |
| --- | --- |
| `embeddings_store.py push` の 0 行ガード | 空のファイルを上げてしまう |
| `push` の縮小ガード（前回の 9 割未満で中止） | 別プロジェクトのファイルを取り違える |
| `build_index.py --require-vectors` | `pull` を忘れたまま、または埋め込みが古いままデプロイする |

`--require-vectors` は Dockerfile に入れてあるので、**ビルドの時点で失敗します**。
判定は「マニフェストがあるか」で切り替わります。

- マニフェストが無い → ベクトルを使わない構成として通す（全文検索だけで動く）
- マニフェストがある → 実体が無ければ失敗。ベクトルの充足率が 98% を切っても失敗

充足率で見ているのは、**規則が改訂されて本文が変わった分の埋め込みが
未取得**という状態を捕まえるためです。この場合は `build_embeddings.py` の
実行が必要で、`pull` では直りません。エラーメッセージに手順を出します。

## 費用

| 項目 | 概算 |
| --- | --- |
| 保管（34MB・Standard・us-west1） | 月 0.1 円未満 |
| 世代を 20 個保持（約 700MB） | 月 2 円程度 |
| `pull` 1 回の下り転送（34MB） | 0.6 円程度 |

無視できる額です。移設の目的は費用ではなくリポジトリの肥大化防止です。

## GitHub Actions から更新する場合

日次同期に埋め込みの更新を組み込むなら、Actions から GCS へ書く認証が必要です。
サービスアカウントキーをリポジトリの Secrets に置くのではなく、
**Workload Identity 連携**を使ってください（`deploy.md` 6 章と同じ仕組み）。

```yaml
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: projects/.../providers/github
          service_account: jaf-regulations-sync@....iam.gserviceaccount.com
      - uses: google-github-actions/setup-gcloud@v2

      - name: 埋め込みを更新
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: |
          python pipeline/build_index.py
          python pipeline/build_embeddings.py
          python pipeline/embeddings_store.py push
```

そのうえで、コミット対象は `data/embeddings.manifest.json` だけになります
（実体は `.gitignore` で外れます）。サービスアカウントには
`roles/storage.objectAdmin` をこのバケットに限って与えてください。

## 次の候補: 図版

`content/` の 101MB のうち大半は図版 1,489 点です。これも GCS に移せますが、
**配信経路が変わる**ので影響範囲が違います。フロントエンドの画像 URL、
キャッシュ制御、バケットの公開設定を触ることになるため、
埋め込みとは別に検討してください（`architecture.md` の工程表 12 番）。
