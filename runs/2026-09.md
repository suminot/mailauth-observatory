# 月次計測 2026-09

- 状態: **partial**
- 集計時刻: 2026-10-24T11:03:24+00:00
- 警告: 20 件

## 工程

| 工程 | 状態 | 入力 | 成功 | 失敗 | スキップ | 秒 |
|---|---|---|---|---|---|---|
| p1_population 母集団確定 | success | 11427 | 3813 | 0 | 7614 | 3.998 |
| p2_candidates ドメイン候補生成 | partial | 3813 | 3203 | 3788 | 610 | 1666.295 |
| p3_domains メールドメイン確定 | partial | 3634 | 3262 | 6 | 372 | 609.081 |
| p4_measure DNS計測 | success | 3262 | 10 | 0 | 3248 | 74.683 |
| p5_parse パース | success | 201452 | 3258 | 0 | 0 | 768.0 |
| p6_infer 推察 | success | 3258 | 9608 | 0 | 0 | 1.458 |
| p7_aggregate 集計 | success | 3258 | 13 | 0 | 0 | 0.481 |
| p8_publish 公開 | success | 62 | 125 | 0 | 0 | 0.048 |

## 工程間の件数

どこで何件落ちたかを見るための表である。出力と入力がずれること自体は
正常で（P3 の絞り込みなど）、見るのはその大きさ。
件数の単位はどの工程もドメインで揃えてある。

| 前 | 後 | 前の成功 | 後の入力 | 差 |
|---|---|---|---|---|
| p1_population | p2_candidates | 3813 | 3813 | 0 |
| p2_candidates | p3_domains | 3203 | 3634 | 431 |
| p3_domains | p4_measure | 3262 | 3262 | 0 |
| p4_measure | p5_parse | 10 | 201452 | 201442 |
| p5_parse | p6_infer | 3258 | 3258 | 0 |
| p6_infer | p7_aggregate | 9608 | 3258 | -6350 |
| p7_aggregate | p8_publish | 13 | 62 | 49 |

## 警告

### p1_population

- `ENRICH_SKIPPED_HOUJIN_BANGOU`: MAILAUTH_HOUJIN_BANGOU_APP_ID が未設定のため商号・所在地の裏取りをしていない
- `WIKIDATA_NAME_MISMATCH` ×3: securities_code で鍵は当たったが社名が一致しない項目を 3 件使わなかった。**別の会社のサイトを紐づけないための歯止めである。**多すぎるなら鍵か国の絞り込みを見直すこと
- `NO_PREVIOUS_RUN`: 前月 2026-08 の entities.parquet が無いため差分を計算していない
- `ACCEPTANCE_MISSING_RATE`: official_url の欠損率 16.0% が閾値 10.0% を超えている

### p2_candidates

- `SHARED_RUA_DOMAIN_DROPPED`: 複数企業の rua 宛先になっているドメインを候補から落とした。**第三者のレポート処理サービスを企業のドメインとして計測しない。** ベンダー名が分かれば configs/vendors/dmarc_rua_vendors.yaml に、本当にグループ共用なら configs/domains/manual_domains.csv に足すこと
- `DOMAIN_SHARED_BY_ENTITIES` ×3: 同一ドメインが複数企業に紐づいている。持株会社・グループ共用の可能性
- `UNALIGNED_RUA_TARGET` ×158: 自社ドメインでない rua 宛先があった。**候補にしていない。** 第三者のレポート処理サービスであり、その企業の送信ドメインではない。ベンダー名が分かれば configs/vendors/dmarc_rua_vendors.yaml に足すと P6 の dmarc_vendor 推定が埋まる
- `CT_UPSTREAM_UNAVAILABLE`: crt.sh に断られた回数（20）が、答えが返った回数（9）を上回っている。**この月の CT 由来の候補はキャッシュにあった分だけ**で、少ないのは実態ではなく相手が落ちていたためである。crt.sh が戻ってから同じ run_id で流し直すと、キャッシュに積み上がっている分はそのまま使われる
- `ACCEPTANCE_ZERO_CANDIDATES`: 候補ゼロの企業が 16.0% で閾値 5.0% を超えている

### p4_measure

- `RESUMED_FROM_EXISTING_BRONZE` ×3248: 同じ run の bronze に 3248 ドメインぶんが既にあったため測り直していない。**「観測していない」のではなく「前回の実行で観測済み」である**
- `BRONZE_APPENDED_TO_EXISTING` ×5: 同じ run に既存の bronze があったため、上書きせず新しいパートに追記した（bronze は不変・追記のみ）。--resume なので、既に測り終えたドメインは測り直していない
- `WILDCARD_DNS`: 実在しないセレクタに応答したドメイン。何にでも答える DNS のため DKIM の検出結果は偽陽性の可能性がある

### p5_parse

- `DKIM_WILDCARD_SUSPECT` ×10: 対照クエリが応答したドメイン。DKIM の検出は偽陽性の可能性
- `RUA_DOMAIN_UNREGISTERED`: rua の宛先ドメインが未登録。第三者が登録すればレポートが漏洩しうる（Hureau et al., PAM 2024）

### p6_infer

- `NO_STALE_HISTORY`: 前月の inferences.parquet が無いため stale の連続月数を引き継げない。3 か月連続の判定は今月を1か月目として数え直す
- `UNKNOWN_MX_HOSTS` ×100: 辞書に無い MX ホストが 125 種、253 ドメインぶんある。**一覧に出しているのは上位 100 種まで**で、ほかに 25 種（25 ドメイン）が載っていない。**自社ドメイン配下の MX 485 ドメイン（404 社）は一覧から外している** ── 辞書に足してもその1社にしか効かない。頻度順の上位を手で辞書に追記すると推定率が上がる（DESIGN.md P6 実装メモ）
- `SECURITY_GATEWAY_UNDETECTABLE` ×2114: セキュリティ製品を DNS 上で検出できなかったドメイン。**理由の内訳: api_mode_product 1738件、self_hosted_mx 337件、spf_flattened 39件、tenant_placeholder_mx 3件。** どれも「使っていない」と解釈してはならない

### p7_aggregate

- `NO_PREVIOUS_FACTS`: 前月 2026-08 の facts が無いため差分を計算していない
- `GOLD_FROM_DEGRADED_RUN`: P2 は相手が落ちていた（CT_UPSTREAM_UNAVAILABLE）が、既にある gold より分母が小さくならないので書いた。CT 由来の候補はキャッシュにあった分だけである

### p8_publish

- `TIER2_DISABLED`: 第2層は無効。個社名付き明細は出力していない。有効化には事前通知から最低 30 日の訂正期間とアクセス制御の設定が必要

