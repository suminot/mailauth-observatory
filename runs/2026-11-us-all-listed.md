# 月次計測 2026-11（us-all-listed）

- 母集団: `us-all-listed`
- 状態: **partial**
- 集計時刻: 2026-11-04T14:21:56+00:00
- 警告: 25 件

## 工程

| 工程 | 状態 | 入力 | 成功 | 失敗 | スキップ | 秒 |
|---|---|---|---|---|---|---|
| p1_population 母集団確定 | success | 6059 | 5254 | 0 | 0 | 1790.462 |
| p2_candidates ドメイン候補生成 | partial | 5254 | 5197 | 29 | 57 | 5744.155 |
| p3_domains メールドメイン確定 | partial | 11848 | 11044 | 142 | 804 | 1777.505 |
| p4_measure DNS計測 | success | 11044 | 0 | 0 | 10904 | 8.995 |
| p5_parse パース | success | 452008 | 10801 | 0 | 0 | 566.296 |
| p6_infer 推察 | success | 10801 | 36324 | 0 | 0 | 4.396 |
| p7_aggregate 集計 | success | 10801 | 13 | 0 | 0 | 1.129 |
| p8_publish 公開 | success | 306 | 2223 | 0 | 0 | 0.077 |

## 工程間の件数

どこで何件落ちたかを見るための表である。出力と入力がずれること自体は
正常で（P3 の絞り込みなど）、見るのはその大きさ。
件数の単位はどの工程もドメインで揃えてある。

| 前 | 後 | 前の成功 | 後の入力 | 差 |
|---|---|---|---|---|
| p1_population | p2_candidates | 5254 | 5254 | 0 |
| p2_candidates | p3_domains | 5197 | 11848 | 6651 |
| p3_domains | p4_measure | 11044 | 11044 | 0 |
| p4_measure | p5_parse | 0 | 452008 | 452008 |
| p5_parse | p6_infer | 10801 | 10801 | 0 |
| p6_infer | p7_aggregate | 36324 | 10801 | -25523 |
| p7_aggregate | p8_publish | 13 | 306 | 293 |

## 警告

### p1_population

- `NON_OPERATING_EXCLUDED` ×805: 事業会社でないもの（SPAC・ファンド・ETF）を母集団から外した。**「測れなかった」ではなく「測らないと決めた」**。名前と理由は excluded_entities.parquet に残し、公開する
- `WIKIDATA_NAME_MISMATCH` ×143: ticker で鍵は当たったが社名が一致しない項目を 143 件使わなかった。**別の会社のサイトを紐づけないための歯止めである。**多すぎるなら鍵か国の絞り込みを見直すこと
- `ENRICH_FAILED_SEC_PRESS_RELEASE`: プレスリリースを取得できなかったものがある。**「書いていなかった」ではなく「読めなかった」**（原則5）
- `INDUSTRY_NOT_PROVIDED` ×52: 一次情報（SEC）に業種が入っていない企業がある。**写像を足しても直らない。** ファンド・投資信託や ADR の提出者で、SEC 側に業種が無いか 0000 / 8880 が入っている
- `NO_PREVIOUS_RUN`: 前月 2026-10 の entities.parquet が無いため差分を計算していない

### p2_candidates

- `SHARED_RUA_DOMAIN_DROPPED` ×6: 複数企業の rua 宛先になっているドメインを候補から落とした。**第三者のレポート処理サービスを企業のドメインとして計測しない。** ベンダー名が分かれば configs/vendors/dmarc_rua_vendors.yaml に、本当にグループ共用なら configs/domains/manual_domains.csv に足すこと
- `DOMAIN_SHARED_BY_ENTITIES` ×93: 同一ドメインが複数企業に紐づいている。持株会社・グループ共用の可能性
- `UNALIGNED_RUA_TARGET` ×262: 自社ドメインでない rua 宛先があった。**候補にしていない。** 第三者のレポート処理サービスであり、その企業の送信ドメインではない。ベンダー名が分かれば configs/vendors/dmarc_rua_vendors.yaml に足すと P6 の dmarc_vendor 推定が埋まる

### p4_measure

- `RESUMED_FROM_EXISTING_BRONZE` ×10904: 同じ run の bronze に 10904 ドメインぶんが既にあったため測り直していない。**「観測していない」のではなく「前回の実行で観測済み」である**
- `BRONZE_APPENDED_TO_EXISTING` ×15: 同じ run に既存の bronze があったため、上書きせず新しいパートに追記した（bronze は不変・追記のみ）。--resume なので、既に測り終えたドメインは測り直していない

### p5_parse

- `BRONZE_OUTSIDE_MEASURED_SET` ×103: 同じ run の bronze に、いまの P3 が計測対象にしていないドメインがあった（前の試行の残り）。facts に入れていない
- `DKIM_WILDCARD_SUSPECT` ×82: 対照クエリが応答したドメイン。DKIM の検出は偽陽性の可能性
- `RUA_DOMAIN_UNREGISTERED` ×3: rua の宛先ドメインが未登録。第三者が登録すればレポートが漏洩しうる（Hureau et al., PAM 2024）
- `MTA_STS_POLICY_UNUSABLE` ×26: MTA-STS の TXT はあるが、ポリシーを取れない・読めないドメイン。**送る側の MTA も同じく読めないので、MTA-STS は効いていない**
- `MTA_STS_MX_MISMATCH` ×6: MTA-STS のポリシーの mx に、実際の MX が入っていない。**enforce なら、対応している送り手からのメールが届かない**
- `MX_UNREACHABLE` ×97: どの MX ホストにも宛先が無い（名前が無い、またはアドレスが無い）ドメイン。**このドメイン宛のメールは届かない。** 終わったサービスや綴りの誤りを指したままのものがある
- `MX_DOMAIN_UNREGISTERED` ×3: MX ホストの登録ドメインが未登録。**第三者が登録すれば、その企業のドメイン宛のメールを受け取れる**（rua の未登録と同じ形）

### p6_infer

- `NO_STALE_HISTORY`: 前月の inferences.parquet が無いため stale の連続月数を引き継げない。3 か月連続の判定は今月を1か月目として数え直す
- `NO_INFERENCE_RATE_HIGH` ×3757: 推定が付かないドメインが 34.8% で受け入れ基準（20% 未満）を超えている。unknown_mx_hosts から辞書を拡充する
- `UNKNOWN_MX_HOSTS` ×100: 辞書に無い MX ホストが 284 種、957 ドメインぶんある。**一覧に出しているのは上位 100 種まで**で、ほかに 184 種（244 ドメイン）が載っていない。**自社ドメイン配下の MX 511 ドメイン（308 社）は一覧から外している** ── 辞書に足してもその1社にしか効かない。**名前の無い MX ホスト 153 ドメインぶんも外している**（製品ではない。P5 の MX_UNREACHABLE を見ること）。頻度順の上位を手で辞書に追記すると推定率が上がる（DESIGN.md P6 実装メモ）
- `SECURITY_GATEWAY_UNDETECTABLE` ×8418: セキュリティ製品を DNS 上で検出できなかったドメイン。**理由の内訳: api_mode_product 7840件、self_hosted_mx 272件、spf_flattened 306件、tenant_placeholder_mx 2件。** どれも「使っていない」と解釈してはならない

### p7_aggregate

- `COMMON12_UNMAPPED` ×52: 共通12分類が付いていない企業は業種別集計から外した。業種不明を1つの業種として扱うと分母が歪む
- `NO_PREVIOUS_FACTS`: 前月 2026-10 の facts が無いため差分を計算していない
- `GOLD_OTHER_POPULATIONS_KEPT` ×85: 同じ月の gold にあった他の母集団の行を残した: jp-all-listed

### p8_publish

- `TIER2_DISABLED`: 第2層は無効。個社名付き明細は出力していない。有効化には事前通知から最低 30 日の訂正期間とアクセス制御の設定が必要

