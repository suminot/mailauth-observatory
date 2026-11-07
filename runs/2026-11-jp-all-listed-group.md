# 月次計測 2026-11（jp-all-listed-group）

- 母集団: `jp-all-listed-group`
- 状態: **success**
- 集計時刻: 2026-11-07T18:27:05+00:00
- 警告: 13 件

## 工程

| 工程 | 状態 | 入力 | 成功 | 失敗 | スキップ | 秒 |
|---|---|---|---|---|---|---|
| p1_population 母集団確定 | success | 486 | 270 | 0 | 0 | 1.552 |
| p2_candidates ドメイン候補生成 | success | 270 | 270 | 0 | 0 | 89.403 |
| p3_domains メールドメイン確定 | success | 831 | 320 | 0 | 511 | 127.689 |
| p4_measure DNS計測 | success | 320 | 320 | 0 | 0 | 233.584 |
| p5_parse パース | success | 18254 | 320 | 0 | 0 | 28.478 |
| p6_infer 推察 | success | 320 | 780 | 0 | 0 | 0.306 |
| p7_aggregate 集計 | success | 320 | 2 | 0 | 0 | 0.168 |
| p8_publish 公開 | success | 405 | 2421 | 0 | 0 | 0.126 |

## 工程間の件数

どこで何件落ちたかを見るための表である。出力と入力がずれること自体は
正常で（P3 の絞り込みなど）、見るのはその大きさ。
件数の単位はどの工程もドメインで揃えてある。

| 前 | 後 | 前の成功 | 後の入力 | 差 |
|---|---|---|---|---|
| p1_population | p2_candidates | 270 | 270 | 0 |
| p2_candidates | p3_domains | 270 | 831 | 561 |
| p3_domains | p4_measure | 320 | 320 | 0 |
| p4_measure | p5_parse | 320 | 18254 | 17934 |
| p5_parse | p6_infer | 320 | 320 | 0 |
| p6_infer | p7_aggregate | 780 | 320 | -460 |
| p7_aggregate | p8_publish | 2 | 405 | 403 |

## 警告

### p1_population

- `NO_PREVIOUS_RUN`: 前月 2026-10 の entities.parquet が無いため差分を計算していない

### p2_candidates

- `UNALIGNED_RUA_TARGET` ×22: 自社ドメインでない rua 宛先があった。**候補にしていない。** 第三者のレポート処理サービスであり、その企業の送信ドメインではない。ベンダー名が分かれば configs/vendors/dmarc_rua_vendors.yaml に足すと P6 の dmarc_vendor 推定が埋まる

### p3_domains

- `ACCEPTANCE_ENTITIES_WITHOUT_CONFIRMED`: confirmed ドメインを持たない企業が 20.0% で閾値 15.0% を超えている。手動確認の候補

### p4_measure

- `RESUME_FOUND_NOTHING`: --resume を指定したが、続きから測れる bronze が無かった。最初から測っている（bronze が消えている可能性がある）

### p5_parse

- `MX_UNREACHABLE` ×2: どの MX ホストにも宛先が無い（名前が無い、またはアドレスが無い）ドメイン。**このドメイン宛のメールは届かない。** 終わったサービスや綴りの誤りを指したままのものがある

### p6_infer

- `NO_STALE_HISTORY`: 前月の inferences.parquet が無いため stale の連続月数を引き継げない。3 か月連続の判定は今月を1か月目として数え直す
- `NO_INFERENCE_RATE_HIGH` ×79: 推定が付かないドメインが 24.7% で受け入れ基準（20% 未満）を超えている。unknown_mx_hosts から辞書を拡充する
- `UNKNOWN_MX_HOSTS` ×18: 辞書に無い MX ホストが 18 種、37 ドメインぶんある。**全部を一覧に出している。****自社ドメイン配下の MX 73 ドメイン（56 社）は一覧から外している** ── 辞書に足してもその1社にしか効かない。**名前の無い MX ホスト 1 ドメインぶんも外している**（製品ではない。P5 の MX_UNREACHABLE を見ること）。頻度順の上位を手で辞書に追記すると推定率が上がる（DESIGN.md P6 実装メモ）
- `SECURITY_GATEWAY_UNDETECTABLE` ×260: セキュリティ製品を DNS 上で検出できなかったドメイン。**理由の内訳: api_mode_product 195件、self_hosted_mx 64件、spf_flattened 1件。** どれも「使っていない」と解釈してはならない

### p7_aggregate

- `COMMON12_UNMAPPED` ×318: 共通12分類が付いていない企業は業種別集計から外した。業種不明を1つの業種として扱うと分母が歪む
- `NO_PREVIOUS_FACTS`: 前月 2026-10 の facts が無いため差分を計算していない
- `GOLD_OTHER_POPULATIONS_KEPT` ×241: 同じ月の gold にあった他の母集団の行を残した: jp-all-listed, jp-nikkei225, us-all-listed

### p8_publish

- `TIER2_DISABLED`: 第2層は無効。個社名付き明細は出力していない。有効化には事前通知から最低 30 日の訂正期間とアクセス制御の設定が必要

