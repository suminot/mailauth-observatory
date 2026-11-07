# 月次計測 2026-11（jp-all-listed-group）

- 母集団: `jp-all-listed-group`
- 状態: **partial**
- 集計時刻: 2026-11-07T22:14:05+00:00
- 警告: 21 件

## 工程

| 工程 | 状態 | 入力 | 成功 | 失敗 | スキップ | 秒 |
|---|---|---|---|---|---|---|
| p1_population 母集団確定 | success | 1687 | 1180 | 0 | 0 | 8.605 |
| p2_candidates ドメイン候補生成 | success | 1180 | 1180 | 0 | 0 | 385.4 |
| p3_domains メールドメイン確定 | partial | 3738 | 1440 | 24 | 2298 | 564.029 |
| p4_measure DNS計測 | success | 1405 | 53 | 0 | 1356 | 40.88 |
| p5_parse パース | success | 82016 | 1440 | 0 | 0 | 45.616 |
| p6_infer 推察 | success | 1440 | 3717 | 0 | 0 | 1.129 |
| p7_aggregate 集計 | success | 1440 | 2 | 0 | 0 | 0.45 |
| p8_publish 公開 | success | 444 | 2499 | 0 | 0 | 0.157 |

## 工程間の件数

どこで何件落ちたかを見るための表である。出力と入力がずれること自体は
正常で（P3 の絞り込みなど）、見るのはその大きさ。
件数の単位はどの工程もドメインで揃えてある。

| 前 | 後 | 前の成功 | 後の入力 | 差 |
|---|---|---|---|---|
| p1_population | p2_candidates | 1180 | 1180 | 0 |
| p2_candidates | p3_domains | 1180 | 3738 | 2558 |
| p3_domains | p4_measure | 1440 | 1405 | -35 |
| p4_measure | p5_parse | 53 | 82016 | 81963 |
| p5_parse | p6_infer | 1440 | 1440 | 0 |
| p6_infer | p7_aggregate | 3717 | 1440 | -2277 |
| p7_aggregate | p8_publish | 2 | 444 | 442 |

## 警告

### p1_population

- `NO_PREVIOUS_RUN`: 前月 2026-10 の entities.parquet が無いため差分を計算していない

### p2_candidates

- `DOMAIN_SHARED_BY_ENTITIES` ×30: 同一ドメインが複数企業に紐づいている。持株会社・グループ共用の可能性
- `UNALIGNED_RUA_TARGET` ×84: 自社ドメインでない rua 宛先があった。**候補にしていない。** 第三者のレポート処理サービスであり、その企業の送信ドメインではない。ベンダー名が分かれば configs/vendors/dmarc_rua_vendors.yaml に足すと P6 の dmarc_vendor 推定が埋まる

### p3_domains

- `ACCEPTANCE_ENTITIES_WITHOUT_CONFIRMED`: confirmed ドメインを持たない企業が 17.0% で閾値 15.0% を超えている。手動確認の候補

### p4_measure

- `RESUMED_FROM_EXISTING_BRONZE` ×1356: 同じ run の bronze に 1356 ドメインぶんが既にあったため測り直していない。**「観測していない」のではなく「前回の実行で観測済み」である**
- `BRONZE_APPENDED_TO_EXISTING` ×4: 同じ run に既存の bronze があったため、上書きせず新しいパートに追記した（bronze は不変・追記のみ）。--resume なので、既に測り終えたドメインは測り直していない
- `WILDCARD_DNS` ×2: 実在しないセレクタに応答したドメイン。何にでも答える DNS のため DKIM の検出結果は偽陽性の可能性がある

### p5_parse

- `BRONZE_OUTSIDE_MEASURED_SET` ×4: 同じ run の bronze に、いまの P3 が計測対象にしていないドメインがあった（前の試行の残り）。facts に入れていない
- `DKIM_WILDCARD_SUSPECT` ×5: 対照クエリが応答したドメイン。DKIM の検出は偽陽性の可能性
- `RUA_DOMAIN_UNREGISTERED` ×2: rua の宛先ドメインが未登録。第三者が登録すればレポートが漏洩しうる（Hureau et al., PAM 2024）
- `MTA_STS_POLICY_UNUSABLE`: MTA-STS の TXT はあるが、ポリシーを取れない・読めないドメイン。**送る側の MTA も同じく読めないので、MTA-STS は効いていない**
- `MX_UNREACHABLE` ×13: どの MX ホストにも宛先が無い（名前が無い、またはアドレスが無い）ドメイン。**このドメイン宛のメールは届かない。** 終わったサービスや綴りの誤りを指したままのものがある
- `MX_DOMAIN_UNREGISTERED` ×4: MX ホストの登録ドメインが未登録。**第三者が登録すれば、その企業のドメイン宛のメールを受け取れる**（rua の未登録と同じ形）

### p6_infer

- `NO_STALE_HISTORY`: 前月の inferences.parquet が無いため stale の連続月数を引き継げない。3 か月連続の判定は今月を1か月目として数え直す
- `NO_INFERENCE_RATE_HIGH` ×321: 推定が付かないドメインが 22.3% で受け入れ基準（20% 未満）を超えている。unknown_mx_hosts から辞書を拡充する
- `UNKNOWN_MX_HOSTS` ×75: 辞書に無い MX ホストが 75 種、189 ドメインぶんある。**全部を一覧に出している。****自社ドメイン配下の MX 289 ドメイン（211 社）は一覧から外している** ── 辞書に足してもその1社にしか効かない。**名前の無い MX ホスト 20 ドメインぶんも外している**（製品ではない。P5 の MX_UNREACHABLE を見ること）。頻度順の上位を手で辞書に追記すると推定率が上がる（DESIGN.md P6 実装メモ）
- `SECURITY_GATEWAY_UNDETECTABLE` ×1120: セキュリティ製品を DNS 上で検出できなかったドメイン。**理由の内訳: api_mode_product 872件、self_hosted_mx 245件、spf_flattened 3件。** どれも「使っていない」と解釈してはならない

### p7_aggregate

- `COMMON12_UNMAPPED` ×1408: 共通12分類が付いていない企業は業種別集計から外した。業種不明を1つの業種として扱うと分母が歪む
- `NO_PREVIOUS_FACTS`: 前月 2026-10 の facts が無いため差分を計算していない
- `GOLD_OTHER_POPULATIONS_KEPT` ×241: 同じ月の gold にあった他の母集団の行を残した: jp-all-listed, jp-nikkei225, us-all-listed

### p8_publish

- `TIER2_DISABLED`: 第2層は無効。個社名付き明細は出力していない。有効化には事前通知から最低 30 日の訂正期間とアクセス制御の設定が必要

