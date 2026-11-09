# 月次計測 2026-11（jp-all-listed-group）

- 母集団: `jp-all-listed-group`
- 状態: **partial**
- 集計時刻: 2026-11-09T00:48:30+00:00
- 警告: 21 件

## 工程

| 工程 | 状態 | 入力 | 成功 | 失敗 | スキップ | 秒 |
|---|---|---|---|---|---|---|
| p1_population 母集団確定 | success | 1802 | 1277 | 0 | 0 | 1644.5 |
| p2_candidates ドメイン候補生成 | success | 1277 | 1277 | 0 | 0 | 399.861 |
| p3_domains メールドメイン確定 | partial | 4286 | 1802 | 33 | 2484 | 641.302 |
| p4_measure DNS計測 | success | 1723 | 172 | 0 | 1575 | 67.135 |
| p5_parse パース | success | 96708 | 1802 | 0 | 0 | 107.33 |
| p6_infer 推察 | success | 1802 | 4496 | 0 | 0 | 1.543 |
| p7_aggregate 集計 | success | 1802 | 2 | 0 | 0 | 0.606 |
| p8_publish 公開 | success | 449 | 2509 | 0 | 0 | 0.189 |

## 工程間の件数

どこで何件落ちたかを見るための表である。出力と入力がずれること自体は
正常で（P3 の絞り込みなど）、見るのはその大きさ。
件数の単位はどの工程もドメインで揃えてある。

| 前 | 後 | 前の成功 | 後の入力 | 差 |
|---|---|---|---|---|
| p1_population | p2_candidates | 1277 | 1277 | 0 |
| p2_candidates | p3_domains | 1277 | 4286 | 3009 |
| p3_domains | p4_measure | 1802 | 1723 | -79 |
| p4_measure | p5_parse | 172 | 96708 | 96536 |
| p5_parse | p6_infer | 1802 | 1802 | 0 |
| p6_infer | p7_aggregate | 4496 | 1802 | -2694 |
| p7_aggregate | p8_publish | 2 | 449 | 447 |

## P1 の起点ドメインの内訳

- `group`: rows 9,289・found 1,802・from_wikidata 523・from_co_jp_guess 115・parent_not_listed 0・listed_company 221・listed_domain 132・duplicate_number 25・many_parents 143・group_site 4・entities 1,277
- `group_co_jp_guess`: targets 4,544・attempted 114・cache_hit 4,349・found 114・no_parent_mention 0・deferred 0・no_reading 81・gbizinfo.attempted 4,544・gbizinfo.cache_hit 4,544・gbizinfo.error 0・gbizinfo.with_reading 4,463・guesser.attempted 114・guesser.no_english_name 0・guesser.from_kana 110・guesser.found_from_kana 0・guesser.core_too_short 0・guesser.no_guess_exists 0・guesser.page_unreachable 112・guesser.name_not_on_page 2・guesser.found 0・guesser.found_by_cert_org 0・guesser.cert_lookups 0・guesser.found_other_tld 0・guesser.meta_refresh_followed 0・guesser.dns_queries 624・guesser.pages_fetched 553

## 警告

### p1_population

- `NO_PREVIOUS_RUN`: 前月 2026-10 の entities.parquet が無いため差分を計算していない

### p2_candidates

- `DOMAIN_SHARED_BY_ENTITIES` ×62: 同一ドメインが複数企業に紐づいている。持株会社・グループ共用の可能性
- `UNALIGNED_RUA_TARGET` ×91: 自社ドメインでない rua 宛先があった。**候補にしていない。** 第三者のレポート処理サービスであり、その企業の送信ドメインではない。ベンダー名が分かれば configs/vendors/dmarc_rua_vendors.yaml に足すと P6 の dmarc_vendor 推定が埋まる

### p3_domains

- `ACCEPTANCE_ENTITIES_WITHOUT_CONFIRMED`: confirmed ドメインを持たない企業が 16.2% で閾値 15.0% を超えている。手動確認の候補

### p4_measure

- `RESUMED_FROM_EXISTING_BRONZE` ×1575: 同じ run の bronze に 1575 ドメインぶんが既にあったため測り直していない。**「観測していない」のではなく「前回の実行で観測済み」である**
- `BRONZE_APPENDED_TO_EXISTING` ×10: 同じ run に既存の bronze があったため、上書きせず新しいパートに追記した（bronze は不変・追記のみ）。--resume なので、既に測り終えたドメインは測り直していない
- `WILDCARD_DNS` ×2: 実在しないセレクタに応答したドメイン。何にでも答える DNS のため DKIM の検出結果は偽陽性の可能性がある

### p5_parse

- `BRONZE_OUTSIDE_MEASURED_SET` ×24: 同じ run の bronze に、いまの P3 が計測対象にしていないドメインがあった（前の試行の残り）。facts に入れていない
- `DKIM_WILDCARD_SUSPECT` ×7: 対照クエリが応答したドメイン。DKIM の検出は偽陽性の可能性
- `RUA_DOMAIN_UNREGISTERED` ×3: rua の宛先ドメインが未登録。第三者が登録すればレポートが漏洩しうる（Hureau et al., PAM 2024）
- `MTA_STS_POLICY_UNUSABLE`: MTA-STS の TXT はあるが、ポリシーを取れない・読めないドメイン。**送る側の MTA も同じく読めないので、MTA-STS は効いていない**
- `MX_UNREACHABLE` ×16: どの MX ホストにも宛先が無い（名前が無い、またはアドレスが無い）ドメイン。**このドメイン宛のメールは届かない。** 終わったサービスや綴りの誤りを指したままのものがある
- `MX_DOMAIN_UNREGISTERED` ×5: MX ホストの登録ドメインが未登録。**第三者が登録すれば、その企業のドメイン宛のメールを受け取れる**（rua の未登録と同じ形）

### p6_infer

- `NO_STALE_HISTORY`: 前月の inferences.parquet が無いため stale の連続月数を引き継げない。3 か月連続の判定は今月を1か月目として数え直す
- `NO_INFERENCE_RATE_HIGH` ×485: 推定が付かないドメインが 26.9% で受け入れ基準（20% 未満）を超えている。unknown_mx_hosts から辞書を拡充する
- `UNKNOWN_MX_HOSTS` ×82: 辞書に無い MX ホストが 82 種、207 ドメインぶんある。**全部を一覧に出している。****自社ドメイン配下の MX 366 ドメイン（262 社）は一覧から外している** ── 辞書に足してもその1社にしか効かない。**名前の無い MX ホスト 26 ドメインぶんも外している**（製品ではない。P5 の MX_UNREACHABLE を見ること）。頻度順の上位を手で辞書に追記すると推定率が上がる（DESIGN.md P6 実装メモ）
- `SECURITY_GATEWAY_UNDETECTABLE` ×1454: セキュリティ製品を DNS 上で検出できなかったドメイン。**理由の内訳: api_mode_product 1140件、self_hosted_mx 312件、spf_flattened 2件。** どれも「使っていない」と解釈してはならない

### p7_aggregate

- `COMMON12_UNMAPPED` ×1499: 共通12分類が付いていない企業は業種別集計から外した。業種不明を1つの業種として扱うと分母が歪む
- `NO_PREVIOUS_FACTS`: 前月 2026-10 の facts が無いため差分を計算していない
- `GOLD_OTHER_POPULATIONS_KEPT` ×241: 同じ月の gold にあった他の母集団の行を残した: jp-all-listed, jp-nikkei225, us-all-listed

### p8_publish

- `TIER2_DISABLED`: 第2層は無効。個社名付き明細は出力していない。有効化には事前通知から最低 30 日の訂正期間とアクセス制御の設定が必要

