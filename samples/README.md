# samples

変換品質の検証用に、JAF の PDF を数本ここに置いてください（git には入りません）。

図表が多く、変換が難しいものを選ぶと有効です。例:

- 国内競技車両規則 第5編 細則（安全装備・ロールケージ等。ベクタ図が大量）
- 国内競技規則（本文中心。読み順とヘッダ除去の確認）
- 全日本ラリー選手権統一規則（表が多い）

置いたあとの確認コマンド:

```bash
python pipeline/cli.py probe samples/xxx.pdf --out probe.json --pages 12
python pipeline/cli.py convert-one samples/xxx.pdf --title "テスト" --out /tmp/out
open /tmp/out/xxx/index.html
```
