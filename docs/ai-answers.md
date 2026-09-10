# AI への質問（RAG）

`/ask` の実装と運用手順です。設計の全体像は
[`architecture.md`](./architecture.md) の 6 章を参照してください。

## 何をするものか

質問に関係する条文を検索し、**その条文に書かれていることだけ**を根拠に答えます。

- 回答には必ず出典番号 `[1]` `[2]` が付き、クリックすると本文の該当条へ飛びます。
- 根拠が見つからなければ「該当する条文が見つかりませんでした」と返し、推測しません。
- 回答の末尾に「正式な判断は JAF の原本 PDF をご確認ください。」を必ず添えます。

旧実装（AI Studio 版）は**規則のタイトルと要約だけ**を system prompt に流し込んでいたため、
条文の中身を答えられませんでした。今回はそこを作り直しています。

## 検索の仕組み

根拠チャンクの選び方は 2 系統の順位を **RRF（Reciprocal Rank Fusion）** で融合します。

| 系統 | 得意 |
| --- | --- |
| FTS5 (trigram) | 「第5条」「FIA基準8858」「45mm」のように語がそのまま出る質問 |
| ベクトル (gemini-embedding-001) | 「ヘルメットはどんなものを使えばいい？」のような言い換え |

上位 8 チャンクを参考資料として渡します。

### 日本語の質問を語に切る

日本語の質問は空白で区切られていないため、そのまま FTS に渡すと**文全体が 1 つの
フレーズ**になり 1 件もヒットしません（実装中に踏みました）。
形態素解析器は入れず、**文字種の切れ目**で語を拾っています。

```
「ロールケージの溶接は？」        → ロールケージ / 溶接
「FIA基準8858に合致したFHRシステム」→ 8858 / システム / FIA / FHR / 基準 / 合致
「安全ベルトの取付角度は45度を超えてよいか」→ 取付角度 / ベルト / 安全 / 45
```

ひらがなだけの語（助詞・活用語尾）は捨てます。trigram は 3 文字未満を引けないため、
2 文字の語（「溶接」など）は落として、ベクトル検索と LIKE のフォールバックに任せます。

根拠集めでは取りこぼしを避けたいので語は **OR** で繋ぎます
（`/api/search` の全文検索は絞り込み重視で AND のままです）。

### ベクトル検索に拡張は使わない

8,778 チャンク × 768 次元 = 約 27MB です。この規模なら numpy の行列積で
総当たりして十分速いので、`sqlite-vec` 等は入れていません。
ベクトルは取得時に L2 正規化してあるので、内積がそのままコサイン類似度になります。

> gemini-embedding-001 は出力次元を 3072 未満にすると**正規化されていない**
> ベクトルを返します。`build_embeddings.py` 側で正規化しています。

## 埋め込みの作り方

```bash
export GEMINI_API_KEY=...

python pipeline/build_index.py                      # まず search.db を作る
python pipeline/build_embeddings.py --estimate      # 件数と文字数の見積り
python pipeline/build_embeddings.py --limit 20      # 少量で動作確認
python pipeline/build_embeddings.py                 # 全件
python pipeline/build_index.py                      # ベクトルを search.db に反映
```

ベクトルは **本文のハッシュをキーに** `data/embeddings.sqlite` へ貯まります。

- `search.db` は毎回作り直しますが、本文が変わらないチャンクは**再取得しません**。
- 規則が改訂されて本文が変わったチャンクだけが次回の対象になります。
- このファイル（34MB）の実体は **GCS** にあり、git には
  `data/embeddings.manifest.json` だけをコミットします。取得と更新の手順は
  [`embeddings-storage.md`](./embeddings-storage.md) を参照してください。

```bash
python pipeline/embeddings_store.py pull    # デプロイ前に手元へ落とす
python pipeline/embeddings_store.py push    # 取得した分を GCS へ上げる
python pipeline/embeddings_store.py status  # 手元・マニフェスト・GCS の食い違い
```

## Cloud Run へのデプロイ

API キーは **Secret Manager** に置き、Cloud Run の環境変数として渡します。
リポジトリにもクライアントのバンドルにも入れません。

**この順番どおりに、各ステップの確認まで通してから次へ進んでください。**
途中で失敗したまま先へ進むと、サービス仕様に「存在しないシークレットへの参照」が
残り、以降のあらゆる更新が失敗します（下の「詰まったときの対処」を参照）。

```bash
gcloud config set project gen-lang-client-0036162343

# 0) 初回だけ: Secret Manager API を有効化する（これを忘れると 1 が失敗する）
gcloud services enable secretmanager.googleapis.com

# 0-b) 埋め込みの実体を GCS から落とす（git には入っていない）
python pipeline/embeddings_store.py pull

# 1) シークレットを作る
#    echo は末尾に改行が入りキーが壊れるので printf を使う
printf '%s' "$GEMINI_API_KEY" | gcloud secrets create gemini-api-key --data-file=-
gcloud secrets versions list gemini-api-key          # ← 1 件出ることを確認

# 2) Cloud Run のサービスアカウントに読み取り権限を与える
PROJECT_NUMBER=$(gcloud projects describe gen-lang-client-0036162343 --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding gemini-api-key \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor
gcloud secrets get-iam-policy gemini-api-key         # ← bindings に出ることを確認

# 3) デプロイ
gcloud run deploy jaf-regulations-next \
  --source . --region us-west1 --allow-unauthenticated \
  --memory 1Gi --max-instances 3 \
  --update-secrets GEMINI_API_KEY=gemini-api-key:latest
```

### 詰まったときの対処

```
ERROR: spec.template.spec.containers[0].env[0].value_from.secret_key_ref.name:
Permission denied on secret: projects/.../secrets/gemini-api-key/versions/latest
```

このメッセージは権限の話に見えますが、**シークレット自体が存在しないとき**にも出ます。
まず `gcloud secrets versions list gemini-api-key` で有無を確かめてください。

一度この状態でデプロイすると、リビジョンは失敗してもサービス仕様には
シークレット参照が残るため、`--max-instances` の変更だけでも失敗するようになります。
参照を外せば更新は通ります。

```bash
gcloud run services update jaf-regulations-next --region us-west1 \
  --remove-secrets GEMINI_API_KEY
```

なお、失敗したリビジョンにはトラフィックが流れないので、
**この間も公開中のサイトは直前の正常なリビジョンで動き続けます**（2026-09-10 に実際に確認）。

`--memory 1Gi` にしているのは、起動時にベクトル 27MB をメモリへ読み込むためです
（既定の 512Mi でも動きますが余裕がありません）。

環境変数で切り替えられるもの:

| 変数 | 既定 | 用途 |
| --- | --- | --- |
| `GEMINI_API_KEY` | （なし） | 未設定なら AI 回答は自動的に無効になり、画面にもそう出ます |
| `CHAT_MODEL` | `gemini-3.8-flash` | 回答生成のモデル |
| `EMBED_MODEL` | `gemini-embedding-001` | 埋め込みのモデル |
| `EMBED_DIM` | `768` | 埋め込みの次元（変えたら埋め込みを作り直す） |

## 動作確認

```bash
D=https://jaf-regulations-next-XXXX.us-west1.run.app

curl -s $D/api/ask/status
# {"available":true,"hasApiKey":true,"vectors":8778,"hybrid":true,
#  "chatModel":"gemini-3.8-flash","embedModel":"gemini-embedding-001"}

curl -N -X POST $D/api/ask -H 'Content-Type: application/json' \
  -d '{"question":"ロールケージの材質は？"}'
# event: sources → event: delta（逐次）→ event: done
```

`available: false` のときは `hasApiKey` を見れば、キー未設定かインデックス側かが分かります。
画面には「AI による回答は現在利用できません」と出て、全文検索は使えるままになります。

## 費用と濫用対策

**埋め込み（初回）**: 8,778 チャンク・約 900 万文字で 1 ドル未満です。
以降は変更されたチャンクだけなので、日次更新での増分はごくわずかです。

**回答生成**: 1 回の質問で参考資料 8 チャンク（約 1 万文字）＋回答となります。

### 濫用への歯止め（効果の高い順）

| 手段 | 効果 | 手間 |
| --- | --- | --- |
| **AI Studio の Project Spend Cap** | 月額の上限を金額で設定し、達すると止まる。請求が跳ねない保証になる（反映に約 10 分の遅れあり） | 設定のみ |
| **Cloud Run の `--max-instances`** | 同時処理数＝スループットの上限。インスタンスが増えないので、下記のアプリ側レート制限が実質的に全体へ効くようになる | 設定のみ |
| 根拠 0 件なら生成を呼ばない | 実装済み。規則と無関係な質問は Gemini に到達しない | ― |
| 質問 400 文字・履歴 10 往復 | 実装済み。入力トークンの上限 | ― |
| IP ごとのトークンバケット（30 秒 10 回） | 実装済み。ただしインスタンスごとなので単体では弱い | ― |
| 回答キャッシュ（同一質問を再利用） | 実装済み。同じ質問の連打は 2 回目以降 API を呼ばない（下記） | ― |
| ページ発行の短命トークン | 未実装。curl 直打ちや素朴なスクリプトを弾ける | 小 |
| Cloud Armor のレート制限 | エッジで効く本命。ただし外部 ALB が前提（月 20 ドル程度）で、ドメインマッピング構成からの変更が必要 | 大 |

### 回答キャッシュ

同じ質問には、埋め込みも生成も呼ばずに前回の回答をそのまま返します（TTL 付き LRU）。

- **単発の質問だけ**が対象です。会話の続き（履歴あり）は文脈で答えが変わるためキャッシュしません。
- **根拠 0 件の結果も覚えます**。「今日の天気は？」の連打がいちばん止めたいケースなので、
  ここを外すと効果が薄れます。
- キーは質問を NFKC 正規化＋空白畳み込み＋casefold したもので、
  「ＦＩＡ基準  8858 とは？」と「FIA基準 8858 とは？」は同じ扱いになります。
- キーに **モデル名・埋め込み次元・データ版（`search.db` の `builtAt`）** を混ぜているため、
  規則が更新されて再ビルドされれば自動的に無効になります。
- 失敗・空応答は覚えません（エラーが TTL のあいだ固定されるのを避けるため）。

キャッシュから返した回答には、画面に「同じ質問への回答を再利用しました
（AI は呼び出していません）」と表示されます。SSE の `done` イベントにも `cached: true` が入ります。

| 変数 | 既定 | 用途 |
| --- | --- | --- |
| `ANSWER_CACHE_TTL` | `21600`（6 時間） | 保持時間（秒）。`0` にすると実質無効 |
| `ANSWER_CACHE_SIZE` | `500` | 保持件数。超えたら古いものから捨てる |

`/api/ask/status` の `cache` で件数とヒット率を確認できます。

```bash
curl -s $D/api/ask/status | python3 -m json.tool
# "cache": {"entries": 12, "hits": 34, "misses": 12, "ttlSeconds": 21600}
```

> プロセス内に持つので **Cloud Run のインスタンスごとに別のキャッシュ**になります。
> `--max-instances 3` にしてあるため実用上は効きますが、厳密な共有が必要なら
> Memorystore などの外部キャッシュが必要です。

「根拠 0 件なら生成を呼ばない」は部分的な防御です。規則の語を含む質問
（例:「ロールケージについて詩を書いて」）は条文が引けてしまい生成が走ります。
出力は system instruction で縛っていますが、呼び出しの費用は発生します。
また、ベクトル検索が有効なときは質問ごとに埋め込み API を 1 回叩きます
（1 回あたりごくわずかですが、根拠 0 件でも発生します）。

## 日次更新に組み込む

規則が改訂されると本文が変わったチャンクの埋め込みが未取得になるため、
次の 3 つを実行して GCS とマニフェストを更新します。

```bash
export GEMINI_API_KEY=...
python pipeline/build_index.py            # まず search.db を作り直す
python pipeline/build_embeddings.py       # 変わったチャンクだけ取得
python pipeline/embeddings_store.py push  # GCS とマニフェストを更新
```

忘れたままデプロイしても、`build_index.py --require-vectors` が
ビルド時に止めます（ベクトルの充足率が 98% を切ると失敗）。

これを GitHub Actions に載せるには GCS への書き込み認証が必要です。
サービスアカウントキーを Secrets に置くのではなく Workload Identity 連携を
使ってください。設定例は
[`embeddings-storage.md`](./embeddings-storage.md) にあります。

## 分かっている限界

- **自動変換した本文が根拠**です。変換の誤りはそのまま回答の誤りになります。
  警告のある 13 ページ（`architecture.md` 11 章）は特に注意が必要です。
- 表は「セル区切り」を `|` に落としてチャンクに入れているため、
  複雑な表の読み取りは苦手です。図版は本文に含まれません（キャプションのみ）。
- 年度をまたぐ同一規則は別文書として扱われるため、
  「2026年版では」のような年度の指定は効きません（`architecture.md` 5 章の課題）。
