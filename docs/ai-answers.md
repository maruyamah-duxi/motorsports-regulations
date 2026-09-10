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
- このファイルは git にコミットします（約 27MB）。`.dockerignore` と
  `.gcloudignore` で `data/*` を除外しつつ、これだけ通しています。

## Cloud Run へのデプロイ

API キーは **Secret Manager** に置き、Cloud Run の環境変数として渡します。
リポジトリにもクライアントのバンドルにも入れません。

```bash
# 初回だけ: シークレットを作る
printf '%s' "$GEMINI_API_KEY" | gcloud secrets create gemini-api-key \
  --data-file=- --replication-policy=automatic

# Cloud Run のサービスアカウントに読み取り権限を与える
PROJECT_NUMBER=$(gcloud projects describe gen-lang-client-0036162343 --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding gemini-api-key \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor

# デプロイ
gcloud run deploy jaf-regulations-next \
  --source . --region us-west1 --allow-unauthenticated \
  --memory 1Gi \
  --update-secrets GEMINI_API_KEY=gemini-api-key:latest
```

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
公開エンドポイントから有料 API を呼ぶので、次の歯止めを入れています。

- 質問は 400 文字まで、履歴は 10 往復まで
- IP ごとのトークンバケット（30 秒あたり 10 回）

ただし Cloud Run はインスタンスが増減するため、この制限は**厳密ではありません**。
本格的に絞るなら Cloud Armor などを前段に置いてください。
費用が心配な場合は、Google Cloud の予算アラートを設定しておくことを勧めます。

## 日次更新に組み込む

`.github/workflows/regulations-sync.yml` の「巡回・差分検出・変換」のあとに
次を足すと、変更された条文の埋め込みも自動で追従します。
リポジトリの Secrets に `GEMINI_API_KEY` を登録してください。

```yaml
      - name: 検索インデックスと埋め込みを更新
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: |
          python pipeline/build_index.py
          if [ -n "$GEMINI_API_KEY" ]; then
            python pipeline/build_embeddings.py
            python pipeline/build_index.py   # 取得したベクトルを反映
          fi
```

そのうえで、コミット対象に `data/embeddings.sqlite` を含めてください
（現在の `git add -A content data` に含まれています）。

## 分かっている限界

- **自動変換した本文が根拠**です。変換の誤りはそのまま回答の誤りになります。
  警告のある 13 ページ（`architecture.md` 11 章）は特に注意が必要です。
- 表は「セル区切り」を `|` に落としてチャンクに入れているため、
  複雑な表の読み取りは苦手です。図版は本文に含まれません（キャプションのみ）。
- 年度をまたぐ同一規則は別文書として扱われるため、
  「2026年版では」のような年度の指定は効きません（`architecture.md` 5 章の課題）。
