# 月次計測 2026-11（jp-all-listed-group）

- 母集団: `jp-all-listed-group`
- 状態: **partial**
- 集計時刻: 2026-11-08T09:26:21+00:00
- 警告: 20 件

## 工程

| 工程 | 状態 | 入力 | 成功 | 失敗 | スキップ | 秒 |
|---|---|---|---|---|---|---|
| p1_population 母集団確定 | success | 1687 | 1164 | 0 | 0 | 5.024 |
| p2_candidates ドメイン候補生成 | success | 1164 | 1164 | 0 | 0 | 345.054 |
| p3_domains メールドメイン確定 | partial | 3686 | 1417 | 24 | 2269 | 556.126 |
| p4_measure DNS計測 | success | 1388 | 1 | 0 | 1411 | 16.452 |
| p5_parse パース | success | 82230 | 1417 | 0 | 0 | 86.922 |
| p6_infer 推察 | success | 1417 | 3635 | 0 | 0 | 0.888 |
| p7_aggregate 集計 | success | 1417 | 2 | 0 | 0 | 0.401 |
| p8_publish 公開 | success | 442 | 2495 | 0 | 0 | 0.177 |

## 工程間の件数

どこで何件落ちたかを見るための表である。出力と入力がずれること自体は
正常で（P3 の絞り込みなど）、見るのはその大きさ。
件数の単位はどの工程もドメインで揃えてある。

| 前 | 後 | 前の成功 | 後の入力 | 差 |
|---|---|---|---|---|
| p1_population | p2_candidates | 1164 | 1164 | 0 |
| p2_candidates | p3_domains | 1164 | 3686 | 2522 |
| p3_domains | p4_measure | 1417 | 1388 | -29 |
| p4_measure | p5_parse | 1 | 82230 | 82229 |
| p5_parse | p6_infer | 1417 | 1417 | 0 |
| p6_infer | p7_aggregate | 3635 | 1417 | -2218 |
| p7_aggregate | p8_publish | 2 | 442 | 440 |

## 警告

### p1_population

- `NO_PREVIOUS_RUN`: 前月 2026-10 の entities.parquet が無いため差分を計算していない

### p2_candidates

- `DOMAIN_SHARED_BY_ENTITIES` ×29: 同一ドメインが複数企業に紐づいている。持株会社・グループ共用の可能性
- `UNALIGNED_RUA_TARGET` ×83: 自社ドメインでない rua 宛先があった。**候補にしていない。** 第三者のレポート処理サービスであり、その企業の送信ドメインではない。ベンダー名が分かれば configs/vendors/dmarc_rua_vendors.yaml に足すと P6 の dmarc_vendor 推定が埋まる

### p3_domains

- `ACCEPTANCE_ENTITIES_WITHOUT_CONFIRMED`: confirmed ドメインを持たない企業が 17.1% で閾値 15.0% を超えている。手動確認の候補

### p4_measure

- `RESUMED_FROM_EXISTING_BRONZE` ×1411: 同じ run の bronze に 1411 ドメインぶんが既にあったため測り直していない。**「観測していない」のではなく「前回の実行で観測済み」である**
- `BRONZE_APPENDED_TO_EXISTING` ×7: 同じ run に既存の bronze があったため、上書きせず新しいパートに追記した（bronze は不変・追記のみ）。--resume なので、既に測り終えたドメインは測り直していない

### p5_parse

- `BRONZE_OUTSIDE_MEASURED_SET` ×24: 同じ run の bronze に、いまの P3 が計測対象にしていないドメインがあった（前の試行の残り）。facts に入れていない
- `DKIM_WILDCARD_SUSPECT` ×5: 対照クエリが応答したドメイン。DKIM の検出は偽陽性の可能性
- `RUA_DOMAIN_UNREGISTERED` ×2: rua の宛先ドメインが未登録。第三者が登録すればレポートが漏洩しうる（Hureau et al., PAM 2024）
- `MTA_STS_POLICY_UNUSABLE`: MTA-STS の TXT はあるが、ポリシーを取れない・読めないドメイン。**送る側の MTA も同じく読めないので、MTA-STS は効いていない**
- `MX_UNREACHABLE` ×13: どの MX ホストにも宛先が無い（名前が無い、またはアドレスが無い）ドメイン。**このドメイン宛のメールは届かない。** 終わったサービスや綴りの誤りを指したままのものがある
- `MX_DOMAIN_UNREGISTERED` ×4: MX ホストの登録ドメインが未登録。**第三者が登録すれば、その企業のドメイン宛のメールを受け取れる**（rua の未登録と同じ形）

### p6_infer

- `NO_STALE_HISTORY`: 前月の inferences.parquet が無いため stale の連続月数を引き継げない。3 か月連続の判定は今月を1か月目として数え直す
- `NO_INFERENCE_RATE_HIGH` ×321: 推定が付かないドメインが 22.7% で受け入れ基準（20% 未満）を超えている。unknown_mx_hosts から辞書を拡充する
- `UNKNOWN_MX_HOSTS` ×74: 辞書に無い MX ホストが 74 種、176 ドメインぶんある。**全部を一覧に出している。****自社ドメイン配下の MX 284 ドメイン（211 社）は一覧から外している** ── 辞書に足してもその1社にしか効かない。**名前の無い MX ホスト 20 ドメインぶんも外している**（製品ではない。P5 の MX_UNREACHABLE を見ること）。頻度順の上位を手で辞書に追記すると推定率が上がる（DESIGN.md P6 実装メモ）
- `SECURITY_GATEWAY_UNDETECTABLE` ×1104: セキュリティ製品を DNS 上で検出できなかったドメイン。**理由の内訳: api_mode_product 858件、self_hosted_mx 244件、spf_flattened 2件。** どれも「使っていない」と解釈してはならない

### p7_aggregate

- `COMMON12_UNMAPPED` ×1383: 共通12分類が付いていない企業は業種別集計から外した。業種不明を1つの業種として扱うと分母が歪む
- `NO_PREVIOUS_FACTS`: 前月 2026-10 の facts が無いため差分を計算していない
- `GOLD_OTHER_POPULATIONS_KEPT` ×241: 同じ月の gold にあった他の母集団の行を残した: jp-all-listed, jp-nikkei225, us-all-listed

### p8_publish

- `TIER2_DISABLED`: 第2層は無効。個社名付き明細は出力していない。有効化には事前通知から最低 30 日の訂正期間とアクセス制御の設定が必要

