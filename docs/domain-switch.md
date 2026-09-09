# 独自ドメインを新サービスへ付け替える

`jp.motorsports-regulations.org` を
`jaf-motorsports-regulations-explorer`（AI Studio 製・旧）から
`jaf-regulations-next`（自前イメージ・新）へ向け直す手順です。

## 前提の確認（2026-09-09 実測）

本番ドメインのレスポンスヘッダ:

```
server: Google Frontend
x-cloud-trace-context: 0ab3d0161187e655088bd801b851a37d
x-powered-by: Express
content-length: 99776
```

- Cloudflare などのプロキシは挟まっておらず、**Cloud Run に直結**しています。
- `x-powered-by: Express` は AI Studio が作ったラッパーです。
- `/api/healthz` は `Cannot GET /api/healthz`（Express の 404）を返します。

新サービス側は動作確認済みです。

```
$ curl -s https://jaf-regulations-next-47081956556.us-west1.run.app/api/healthz
{"ok":true,"searchDb":true,"content":true,"dist":true,"revision":"jaf-regulations-next-00002-5kh"}
```

---

## 0. どちらの構成かを見分ける

付け替えの手順が変わるので、最初にこれを確認します。

```bash
gcloud config set project gen-lang-client-0036162343

gcloud beta run domain-mappings list --region us-west1
dig +short jp.motorsports-regulations.org
```

| 見えたもの | 構成 | 進む先 |
| --- | --- | --- |
| domain-mappings に出てくる／`dig` が `ghs.googlehosted.com.` を返す | Cloud Run ドメインマッピング | **ケース A** |
| domain-mappings に出てこない／`dig` が固定 IP（A レコード）を返す | 外部アプリケーション ロードバランサ | **ケース B** |

### 2026-09-09 の実測結果 → **ケース A で確定**

```
$ gcloud beta run domain-mappings list --region us-west1
   DOMAIN                          SERVICE                               REGION
✔  jp.motorsports-regulations.org  jaf-motorsports-regulations-explorer  us-west1

$ dig +short jp.motorsports-regulations.org
ghs.googlehosted.com.
142.250.21.121
```

DNS は `ghs.googlehosted.com` への CNAME で、Cloud Run のドメインマッピングです。
以下は **ケース A** の手順に従ってください。

---

## ケース A: Cloud Run ドメインマッピング

1 つのドメインは 1 つのサービスにしか向けられないため、
**削除してから作り直す**ことになります。その間だけサイトが落ちます。

```bash
# 1. 旧マッピングを削除
gcloud beta run domain-mappings delete \
  --domain jp.motorsports-regulations.org --region us-west1

# 2. 新サービスへ作成
gcloud beta run domain-mappings create \
  --service jaf-regulations-next \
  --domain jp.motorsports-regulations.org --region us-west1

# 3. 証明書の発行状況を見る（CertificateProvisioned が True になるまで）
gcloud beta run domain-mappings describe \
  --domain jp.motorsports-regulations.org --region us-west1 \
  --format="table(status.conditions[].type, status.conditions[].status, status.conditions[].message)"
```

**DNS の変更は不要です。** 同じプロジェクト・同じリージョンなので、
作成後に案内されるレコードは元と同じ `ghs.googlehosted.com` になります
（`create` の出力に出るレコードが今の DNS と一致するか、念のため確認してください）。

### ダウンタイムの見込み → 実測では **0 秒**

事前には「削除〜証明書の再発行まで落ちる」と見込んでいましたが、
2026-09-09 に実施したところ **ダウンタイムは発生しませんでした**。

新しいマッピングの証明書が発行されるまで Google Front End は
**旧サービスを配り続け**、証明書が揃った時点で無停止で切り替わります。

実測のタイムライン（`create` 実行から約 10 分）:

```
14:36:51Z  旧サービス（x-powered-by: Express / 99,776 バイト）
14:39:37Z  旧サービスのまま
14:42:55Z  旧サービスのまま
14:44:57Z  新サービスに切替（Express ヘッダ消滅 / 510 バイト）
```

待っている間の `describe` の状態:

```
DomainRoutable          True
CertificateProvisioned  Unknown   Certificate issuance pending.
Ready                   Unknown   Waiting for certificate provisioning.
Retry                   True      System will retry after 01:00 ...
```

`Retry` の «01:00» は「最大 1 時間後に再試行」の意味で、実際にはもっと早く
（今回は約 10 分で）発行されました。**`Ready` が `Unknown` の間もサイトは
落ちていません**。慌てて切り戻さず待ってください。

とはいえ Google 側の都合で長引く可能性は残るので、
アクセスの少ない時間帯に実施するに越したことはありません。

### 切り戻し

同じ手順で旧サービス名を指定すれば戻せます。旧サービスは消さずに残しておいてください。

```bash
gcloud beta run domain-mappings delete \
  --domain jp.motorsports-regulations.org --region us-west1
gcloud beta run domain-mappings create \
  --service jaf-motorsports-regulations-explorer \
  --domain jp.motorsports-regulations.org --region us-west1
```

---

## ケース B: ロードバランサ

バックエンドサービスのサーバーレス NEG を差し替えるだけで、**無停止**で切り替わります。

```bash
# 新サービス用の NEG を作る
gcloud compute network-endpoint-groups create jaf-next-neg \
  --region us-west1 --network-endpoint-type serverless \
  --cloud-run-service jaf-regulations-next

# 既存のバックエンドサービス名を調べる
gcloud compute backend-services list --global

# 旧 NEG を外して新 NEG を付ける
gcloud compute backend-services add-backend <BACKEND_SERVICE> \
  --global --network-endpoint-group jaf-next-neg \
  --network-endpoint-group-region us-west1
gcloud compute backend-services remove-backend <BACKEND_SERVICE> \
  --global --network-endpoint-group <OLD_NEG> \
  --network-endpoint-group-region us-west1
```

反映は 1〜2 分程度です。

---

## 切り替え後の確認

```bash
D=https://jp.motorsports-regulations.org

curl -s $D/api/healthz
# {"ok":true,"searchDb":true,"content":true,"dist":true,"revision":"jaf-regulations-next-...."}

curl -sI $D/ | grep -i x-powered-by
# 何も出なければ OK（Express のラッパーが外れている）

curl -s "$D/api/documents" | python3 -c "import json,sys;print(json.load(sys.stdin)['count'])"
# 160
```

ブラウザでは以下を確認します。

- トップに全 160 規則がカテゴリ別に並ぶ
- 検索「ロールケージ 溶接」で 65 件ヒットし、条名まで出る
- 規則ページを直接リロードしても 404 にならない
- 図版が本文の正しい位置に出る

---

## 切り替えを急がない選択肢

先に別のサブドメイン（例 `next.motorsports-regulations.org`）へマッピングして
実運用に近い形で確かめてから、本番ドメインを切り替えることもできます。
ただし**本番ドメインの切り替え時のダウンタイムは、ケース A である限り避けられません**。
完全に無停止にしたい場合は、ケース B のロードバランサ構成へ移行してください
（月額 20 ドル程度のコストがかかります）。
