# 月次計測 2026-11（jp-all-listed）

- 母集団: `jp-all-listed`
- 状態: **partial**
- 集計時刻: 2026-11-04T14:26:11+00:00
- 警告: 23 件

## 工程

| 工程 | 状態 | 入力 | 成功 | 失敗 | スキップ | 秒 |
|---|---|---|---|---|---|---|
| p1_population 母集団確定 | success | 11433 | 3812 | 0 | 7621 | 1286.218 |
| p2_candidates ドメイン候補生成 | partial | 3812 | 3599 | 21 | 213 | 3229.661 |
| p3_domains メールドメイン確定 | partial | 5202 | 4801 | 36 | 401 | 778.969 |
| p4_measure DNS計測 | success | 4801 | 0 | 0 | 4817 | 4.711 |
| p5_parse パース | success | 268868 | 4763 | 0 | 0 | 182.739 |
| p6_infer 推察 | success | 4763 | 14522 | 0 | 0 | 3.39 |
| p7_aggregate 集計 | success | 4763 | 13 | 0 | 0 | 0.817 |
| p8_publish 公開 | success | 304 | 2219 | 0 | 0 | 0.132 |

## 工程間の件数

どこで何件落ちたかを見るための表である。出力と入力がずれること自体は
正常で（P3 の絞り込みなど）、見るのはその大きさ。
件数の単位はどの工程もドメインで揃えてある。

| 前 | 後 | 前の成功 | 後の入力 | 差 |
|---|---|---|---|---|
| p1_population | p2_candidates | 3812 | 3812 | 0 |
| p2_candidates | p3_domains | 3599 | 5202 | 1603 |
| p3_domains | p4_measure | 4801 | 4801 | 0 |
| p4_measure | p5_parse | 0 | 268868 | 268868 |
| p5_parse | p6_infer | 4763 | 4763 | 0 |
| p6_infer | p7_aggregate | 14522 | 4763 | -9759 |
| p7_aggregate | p8_publish | 13 | 304 | 291 |

## 警告

### p1_population

- `DELISTED_WITH_SECURITIES_CODE` ×2: 証券コードはあるが、コードリストの上場区分が「非上場」の提出者を母集団に入れていない
- `ENRICH_SKIPPED_HOUJIN_BANGOU`: MAILAUTH_HOUJIN_BANGOU_APP_ID が未設定のため商号・所在地の裏取りをしていない
- `WIKIDATA_NAME_MISMATCH` ×3: securities_code で鍵は当たったが社名が一致しない項目を 3 件使わなかった。**別の会社のサイトを紐づけないための歯止めである。**多すぎるなら鍵か国の絞り込みを見直すこと
- `NO_PREVIOUS_RUN`: 前月 2026-10 の entities.parquet が無いため差分を計算していない

### p2_candidates

- `SHARED_RUA_DOMAIN_DROPPED`: 複数企業の rua 宛先になっているドメインを候補から落とした。**第三者のレポート処理サービスを企業のドメインとして計測しない。** ベンダー名が分かれば configs/vendors/dmarc_rua_vendors.yaml に、本当にグループ共用なら configs/domains/manual_domains.csv に足すこと
- `DOMAIN_SHARED_BY_ENTITIES` ×14: 同一ドメインが複数企業に紐づいている。持株会社・グループ共用の可能性
- `UNALIGNED_RUA_TARGET` ×163: 自社ドメインでない rua 宛先があった。**候補にしていない。** 第三者のレポート処理サービスであり、その企業の送信ドメインではない。ベンダー名が分かれば configs/vendors/dmarc_rua_vendors.yaml に足すと P6 の dmarc_vendor 推定が埋まる
- `ACCEPTANCE_ZERO_CANDIDATES`: 候補ゼロの企業が 5.6% で閾値 5.0% を超えている

### p4_measure

- `RESUMED_FROM_EXISTING_BRONZE` ×4817: 同じ run の bronze に 4817 ドメインぶんが既にあったため測り直していない。**「観測していない」のではなく「前回の実行で観測済み」である**
- `BRONZE_APPENDED_TO_EXISTING` ×13: 同じ run に既存の bronze があったため、上書きせず新しいパートに追記した（bronze は不変・追記のみ）。--resume なので、既に測り終えたドメインは測り直していない

### p5_parse

- `BRONZE_OUTSIDE_MEASURED_SET` ×54: 同じ run の bronze に、いまの P3 が計測対象にしていないドメインがあった（前の試行の残り）。facts に入れていない
- `DKIM_WILDCARD_SUSPECT` ×14: 対照クエリが応答したドメイン。DKIM の検出は偽陽性の可能性
- `RUA_DOMAIN_UNREGISTERED`: rua の宛先ドメインが未登録。第三者が登録すればレポートが漏洩しうる（Hureau et al., PAM 2024）
- `MTA_STS_POLICY_UNUSABLE`: MTA-STS の TXT はあるが、ポリシーを取れない・読めないドメイン。**送る側の MTA も同じく読めないので、MTA-STS は効いていない**
- `MTA_STS_MX_MISMATCH`: MTA-STS のポリシーの mx に、実際の MX が入っていない。**enforce なら、対応している送り手からのメールが届かない**
- `MX_UNREACHABLE` ×17: どの MX ホストにも宛先が無い（名前が無い、またはアドレスが無い）ドメイン。**このドメイン宛のメールは届かない。** 終わったサービスや綴りの誤りを指したままのものがある
- `MX_DOMAIN_UNREGISTERED`: MX ホストの登録ドメインが未登録。**第三者が登録すれば、その企業のドメイン宛のメールを受け取れる**（rua の未登録と同じ形）

### p6_infer

- `NO_STALE_HISTORY`: 前月の inferences.parquet が無いため stale の連続月数を引き継げない。3 か月連続の判定は今月を1か月目として数え直す
- `UNKNOWN_MX_HOSTS` ×100: 辞書に無い MX ホストが 151 種、300 ドメインぶんある。**一覧に出しているのは上位 100 種まで**で、ほかに 51 種（51 ドメイン）が載っていない。**自社ドメイン配下の MX 666 ドメイン（561 社）は一覧から外している** ── 辞書に足してもその1社にしか効かない。**名前の無い MX ホスト 37 ドメインぶんも外している**（製品ではない。P5 の MX_UNREACHABLE を見ること）。頻度順の上位を手で辞書に追記すると推定率が上がる（DESIGN.md P6 実装メモ）
- `SECURITY_GATEWAY_UNDETECTABLE` ×3399: セキュリティ製品を DNS 上で検出できなかったドメイン。**理由の内訳: api_mode_product 2868件、self_hosted_mx 488件、spf_flattened 43件、tenant_placeholder_mx 6件。** どれも「使っていない」と解釈してはならない

### p7_aggregate

- `NO_PREVIOUS_FACTS`: 前月 2026-10 の facts が無いため差分を計算していない
- `GOLD_OTHER_POPULATIONS_KEPT` ×91: 同じ月の gold にあった他の母集団の行を残した: us-all-listed

### p8_publish

- `TIER2_DISABLED`: 第2層は無効。個社名付き明細は出力していない。有効化には事前通知から最低 30 日の訂正期間とアクセス制御の設定が必要

