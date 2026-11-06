# 月次計測 2026-11（jp-all-listed）

- 母集団: `jp-all-listed`
- 状態: **failed**
- 集計時刻: 2026-11-06T21:14:49+00:00
- 警告: 20 件

## 工程

| 工程 | 状態 | 入力 | 成功 | 失敗 | スキップ | 秒 |
|---|---|---|---|---|---|---|
| p1_population 母集団確定 | success | 11445 | 3814 | 0 | 7631 | 2242.467 |
| p2_candidates ドメイン候補生成 | failed | 3814 | 0 | 0 | 0 | 2151.025 |
| p3_domains メールドメイン確定 | partial | 5202 | 4802 | 35 | 400 | 778.522 |
| p4_measure DNS計測 | success | 4802 | 0 | 0 | 4817 | 4.9 |
| p5_parse パース | success | 268868 | 4764 | 0 | 0 | 279.583 |
| p6_infer 推察 | success | 4764 | 14529 | 0 | 0 | 3.327 |
| p7_aggregate 集計 | success | 4764 | 13 | 0 | 0 | 0.879 |
| p8_publish 公開 | success | 305 | 2221 | 0 | 0 | 0.144 |

## 工程間の件数

どこで何件落ちたかを見るための表である。出力と入力がずれること自体は
正常で（P3 の絞り込みなど）、見るのはその大きさ。
件数の単位はどの工程もドメインで揃えてある。

| 前 | 後 | 前の成功 | 後の入力 | 差 |
|---|---|---|---|---|
| p1_population | p2_candidates | 3814 | 3814 | 0 |
| p2_candidates | p3_domains | 0 | 5202 | 5202 |
| p3_domains | p4_measure | 4802 | 4802 | 0 |
| p4_measure | p5_parse | 0 | 268868 | 268868 |
| p5_parse | p6_infer | 4764 | 4764 | 0 |
| p6_infer | p7_aggregate | 14529 | 4764 | -9765 |
| p7_aggregate | p8_publish | 13 | 305 | 292 |

## 警告

### p1_population

- `DELISTED_WITH_SECURITIES_CODE` ×3: 証券コードはあるが、コードリストの上場区分が「非上場」の提出者を母集団に入れていない
- `ENRICH_SKIPPED_HOUJIN_BANGOU`: MAILAUTH_HOUJIN_BANGOU_APP_ID が未設定のため商号・所在地の裏取りをしていない
- `WIKIDATA_NAME_MISMATCH` ×3: securities_code で鍵は当たったが社名が一致しない項目を 3 件使わなかった。**別の会社のサイトを紐づけないための歯止めである。**多すぎるなら鍵か国の絞り込みを見直すこと
- `NO_PREVIOUS_RUN`: 前月 2026-10 の entities.parquet が無いため差分を計算していない

### p4_measure

- `RESUMED_FROM_EXISTING_BRONZE` ×4817: 同じ run の bronze に 4817 ドメインぶんが既にあったため測り直していない。**「観測していない」のではなく「前回の実行で観測済み」である**
- `BRONZE_APPENDED_TO_EXISTING` ×22: 同じ run に既存の bronze があったため、上書きせず新しいパートに追記した（bronze は不変・追記のみ）。--resume なので、既に測り終えたドメインは測り直していない

### p5_parse

- `BRONZE_OUTSIDE_MEASURED_SET` ×53: 同じ run の bronze に、いまの P3 が計測対象にしていないドメインがあった（前の試行の残り）。facts に入れていない
- `DKIM_WILDCARD_SUSPECT` ×14: 対照クエリが応答したドメイン。DKIM の検出は偽陽性の可能性
- `RUA_DOMAIN_UNREGISTERED`: rua の宛先ドメインが未登録。第三者が登録すればレポートが漏洩しうる（Hureau et al., PAM 2024）
- `MTA_STS_POLICY_UNUSABLE`: MTA-STS の TXT はあるが、ポリシーを取れない・読めないドメイン。**送る側の MTA も同じく読めないので、MTA-STS は効いていない**
- `MTA_STS_MX_MISMATCH`: MTA-STS のポリシーの mx に、実際の MX が入っていない。**enforce なら、対応している送り手からのメールが届かない**
- `MX_UNREACHABLE` ×18: どの MX ホストにも宛先が無い（名前が無い、またはアドレスが無い）ドメイン。**このドメイン宛のメールは届かない。** 終わったサービスや綴りの誤りを指したままのものがある
- `MX_DOMAIN_UNREGISTERED`: MX ホストの登録ドメインが未登録。**第三者が登録すれば、その企業のドメイン宛のメールを受け取れる**（rua の未登録と同じ形）

### p6_infer

- `NO_STALE_HISTORY`: 前月の inferences.parquet が無いため stale の連続月数を引き継げない。3 か月連続の判定は今月を1か月目として数え直す
- `UNKNOWN_MX_HOSTS` ×100: 辞書に無い MX ホストが 147 種、284 ドメインぶんある。**一覧に出しているのは上位 100 種まで**で、ほかに 47 種（47 ドメイン）が載っていない。**自社ドメイン配下の MX 665 ドメイン（560 社）は一覧から外している** ── 辞書に足してもその1社にしか効かない。**名前の無い MX ホスト 38 ドメインぶんも外している**（製品ではない。P5 の MX_UNREACHABLE を見ること）。頻度順の上位を手で辞書に追記すると推定率が上がる（DESIGN.md P6 実装メモ）
- `SECURITY_GATEWAY_UNDETECTABLE` ×3396: セキュリティ製品を DNS 上で検出できなかったドメイン。**理由の内訳: api_mode_product 2865件、self_hosted_mx 488件、spf_flattened 43件、tenant_placeholder_mx 6件。** どれも「使っていない」と解釈してはならない

### p7_aggregate

- `DOMAIN_WITHOUT_ENTITY`: entities.parquet に存在しない entity_id のドメインを集計から外した。母集団と計測対象がずれている
- `NO_PREVIOUS_FACTS`: 前月 2026-10 の facts が無いため差分を計算していない
- `GOLD_OTHER_POPULATIONS_KEPT` ×93: 同じ月の gold にあった他の母集団の行を残した: us-all-listed

### p8_publish

- `TIER2_DISABLED`: 第2層は無効。個社名付き明細は出力していない。有効化には事前通知から最低 30 日の訂正期間とアクセス制御の設定が必要


## 失敗した工程

- **p2_candidates**: httpx.InvalidURL: Invalid IDNA hostname: 'www.murata.coｍ'
