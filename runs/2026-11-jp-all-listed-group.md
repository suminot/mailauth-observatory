# 月次計測 2026-11（jp-all-listed-group）

- 母集団: `jp-all-listed-group`
- 状態: **partial**
- 集計時刻: 2026-11-08T15:29:34+00:00
- 警告: 20 件

## 工程

| 工程 | 状態 | 入力 | 成功 | 失敗 | スキップ | 秒 |
|---|---|---|---|---|---|---|
| p1_population 母集団確定 | success | 1802 | 1277 | 0 | 0 | 4683.38 |
| p2_candidates ドメイン候補生成 | success | 1277 | 1277 | 0 | 0 | 399.579 |
| p3_domains メールドメイン確定 | partial | 4076 | 1601 | 24 | 2475 | 613.675 |
| p4_measure DNS計測 | success | 1551 | 52 | 0 | 1523 | 44.539 |
| p5_parse パース | success | 91681 | 1601 | 0 | 0 | 49.347 |
| p6_infer 推察 | success | 1601 | 4069 | 0 | 0 | 1.283 |
| p7_aggregate 集計 | success | 1601 | 2 | 0 | 0 | 0.535 |
| p8_publish 公開 | success | 445 | 2501 | 0 | 0 | 0.169 |

## 工程間の件数

どこで何件落ちたかを見るための表である。出力と入力がずれること自体は
正常で（P3 の絞り込みなど）、見るのはその大きさ。
件数の単位はどの工程もドメインで揃えてある。

| 前 | 後 | 前の成功 | 後の入力 | 差 |
|---|---|---|---|---|
| p1_population | p2_candidates | 1277 | 1277 | 0 |
| p2_candidates | p3_domains | 1277 | 4076 | 2799 |
| p3_domains | p4_measure | 1601 | 1551 | -50 |
| p4_measure | p5_parse | 52 | 91681 | 91629 |
| p5_parse | p6_infer | 1601 | 1601 | 0 |
| p6_infer | p7_aggregate | 4069 | 1601 | -2468 |
| p7_aggregate | p8_publish | 2 | 445 | 443 |

## P1 の起点ドメインの内訳

- `group`: rows 9,289・found 1,802・from_wikidata 523・from_co_jp_guess 115・parent_not_listed 0・listed_company 221・listed_domain 132・duplicate_number 25・many_parents 143・group_site 4・entities 1,277
- `group_co_jp_guess`: targets 4,544・attempted 4,463・cache_hit 0・found 114・no_parent_mention 128・deferred 0・no_reading 81・gbizinfo.attempted 4,544・gbizinfo.cache_hit 4,541・gbizinfo.error 0・gbizinfo.with_reading 4,463・guesser.attempted 4,463・guesser.no_english_name 8・guesser.from_kana 4,412・guesser.found_from_kana 230・guesser.core_too_short 61・guesser.no_guess_exists 3,578・guesser.page_unreachable 114・guesser.name_not_on_page 460・guesser.found 242・guesser.found_by_cert_org 0・guesser.cert_lookups 0・guesser.found_other_tld 80・guesser.meta_refresh_followed 89・guesser.dns_queries 24,705・guesser.pages_fetched 3,201

## 警告

### p1_population

- `NO_PREVIOUS_RUN`: 前月 2026-10 の entities.parquet が無いため差分を計算していない

### p2_candidates

- `DOMAIN_SHARED_BY_ENTITIES` ×49: 同一ドメインが複数企業に紐づいている。持株会社・グループ共用の可能性
- `UNALIGNED_RUA_TARGET` ×91: 自社ドメインでない rua 宛先があった。**候補にしていない。** 第三者のレポート処理サービスであり、その企業の送信ドメインではない。ベンダー名が分かれば configs/vendors/dmarc_rua_vendors.yaml に足すと P6 の dmarc_vendor 推定が埋まる

### p3_domains

- `ACCEPTANCE_ENTITIES_WITHOUT_CONFIRMED`: confirmed ドメインを持たない企業が 16.4% で閾値 15.0% を超えている。手動確認の候補

### p4_measure

- `RESUMED_FROM_EXISTING_BRONZE` ×1523: 同じ run の bronze に 1523 ドメインぶんが既にあったため測り直していない。**「観測していない」のではなく「前回の実行で観測済み」である**
- `BRONZE_APPENDED_TO_EXISTING` ×9: 同じ run に既存の bronze があったため、上書きせず新しいパートに追記した（bronze は不変・追記のみ）。--resume なので、既に測り終えたドメインは測り直していない

### p5_parse

- `BRONZE_OUTSIDE_MEASURED_SET` ×24: 同じ run の bronze に、いまの P3 が計測対象にしていないドメインがあった（前の試行の残り）。facts に入れていない
- `DKIM_WILDCARD_SUSPECT` ×5: 対照クエリが応答したドメイン。DKIM の検出は偽陽性の可能性
- `RUA_DOMAIN_UNREGISTERED` ×3: rua の宛先ドメインが未登録。第三者が登録すればレポートが漏洩しうる（Hureau et al., PAM 2024）
- `MTA_STS_POLICY_UNUSABLE`: MTA-STS の TXT はあるが、ポリシーを取れない・読めないドメイン。**送る側の MTA も同じく読めないので、MTA-STS は効いていない**
- `MX_UNREACHABLE` ×13: どの MX ホストにも宛先が無い（名前が無い、またはアドレスが無い）ドメイン。**このドメイン宛のメールは届かない。** 終わったサービスや綴りの誤りを指したままのものがある
- `MX_DOMAIN_UNREGISTERED` ×5: MX ホストの登録ドメインが未登録。**第三者が登録すれば、その企業のドメイン宛のメールを受け取れる**（rua の未登録と同じ形）

### p6_infer

- `NO_STALE_HISTORY`: 前月の inferences.parquet が無いため stale の連続月数を引き継げない。3 か月連続の判定は今月を1か月目として数え直す
- `NO_INFERENCE_RATE_HIGH` ×372: 推定が付かないドメインが 23.2% で受け入れ基準（20% 未満）を超えている。unknown_mx_hosts から辞書を拡充する
- `UNKNOWN_MX_HOSTS` ×79: 辞書に無い MX ホストが 79 種、189 ドメインぶんある。**全部を一覧に出している。****自社ドメイン配下の MX 340 ドメイン（249 社）は一覧から外している** ── 辞書に足してもその1社にしか効かない。**名前の無い MX ホスト 23 ドメインぶんも外している**（製品ではない。P5 の MX_UNREACHABLE を見ること）。頻度順の上位を手で辞書に追記すると推定率が上がる（DESIGN.md P6 実装メモ）
- `SECURITY_GATEWAY_UNDETECTABLE` ×1268: セキュリティ製品を DNS 上で検出できなかったドメイン。**理由の内訳: api_mode_product 974件、self_hosted_mx 292件、spf_flattened 2件。** どれも「使っていない」と解釈してはならない

### p7_aggregate

- `COMMON12_UNMAPPED` ×1499: 共通12分類が付いていない企業は業種別集計から外した。業種不明を1つの業種として扱うと分母が歪む
- `NO_PREVIOUS_FACTS`: 前月 2026-10 の facts が無いため差分を計算していない
- `GOLD_OTHER_POPULATIONS_KEPT` ×241: 同じ月の gold にあった他の母集団の行を残した: jp-all-listed, jp-nikkei225, us-all-listed

### p8_publish

- `TIER2_DISABLED`: 第2層は無効。個社名付き明細は出力していない。有効化には事前通知から最低 30 日の訂正期間とアクセス制御の設定が必要

