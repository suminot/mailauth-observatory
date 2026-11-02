# 月次計測 2026-11（us-all-listed）

- 母集団: `us-all-listed`
- 状態: **partial**
- 集計時刻: 2026-11-02T06:22:53+00:00
- 警告: 26 件

## 工程

| 工程 | 状態 | 入力 | 成功 | 失敗 | スキップ | 秒 |
|---|---|---|---|---|---|---|
| p1_population 母集団確定 | success | 6055 | 5265 | 0 | 0 | 1981.224 |
| p2_candidates ドメイン候補生成 | partial | 5265 | 4896 | 5253 | 369 | 1112.629 |
| p3_domains メールドメイン確定 | partial | 5670 | 4956 | 9 | 714 | 859.656 |
| p4_measure DNS計測 | success | 4956 | 4956 | 0 | 0 | 4857.153 |
| p5_parse パース | success | 313973 | 4931 | 0 | 0 | 220.379 |
| p6_infer 推察 | success | 4931 | 21664 | 0 | 0 | 3.832 |
| p7_aggregate 集計 | success | 4931 | 13 | 0 | 0 | 1.084 |
| p8_publish 公開 | success | 264 | 2109 | 0 | 0 | 0.118 |

## 工程間の件数

どこで何件落ちたかを見るための表である。出力と入力がずれること自体は
正常で（P3 の絞り込みなど）、見るのはその大きさ。
件数の単位はどの工程もドメインで揃えてある。

| 前 | 後 | 前の成功 | 後の入力 | 差 |
|---|---|---|---|---|
| p1_population | p2_candidates | 5265 | 5265 | 0 |
| p2_candidates | p3_domains | 4896 | 5670 | 774 |
| p3_domains | p4_measure | 4956 | 4956 | 0 |
| p4_measure | p5_parse | 4956 | 313973 | 309017 |
| p5_parse | p6_infer | 4931 | 4931 | 0 |
| p6_infer | p7_aggregate | 21664 | 4931 | -16733 |
| p7_aggregate | p8_publish | 13 | 264 | 251 |

## 警告

### p1_population

- `NON_OPERATING_EXCLUDED` ×790: 事業会社でないもの（SPAC・ファンド・ETF）を母集団から外した。**「測れなかった」ではなく「測らないと決めた」**。名前と理由は excluded_entities.parquet に残し、公開する
- `WIKIDATA_NAME_MISMATCH` ×143: ticker で鍵は当たったが社名が一致しない項目を 143 件使わなかった。**別の会社のサイトを紐づけないための歯止めである。**多すぎるなら鍵か国の絞り込みを見直すこと
- `SEC_ANNUAL_REPORT_SHARED_DOMAIN` ×3: 複数社の年次報告書に同じドメインが書かれていて、社名とも合わないので使わなかった。**IR 代行や運用会社のドメインである可能性が高い。** 第三者と分かったら annual_report.THIRD_PARTY に足す
- `ENRICH_FAILED_SEC_PRESS_RELEASE` ×2: プレスリリースを取得できなかったものがある。**「書いていなかった」ではなく「読めなかった」**（原則5）
- `INDUSTRY_NOT_PROVIDED` ×57: 一次情報（SEC）に業種が入っていない企業がある。**写像を足しても直らない。** ファンド・投資信託や ADR の提出者で、SEC 側に業種が無いか 0000 / 8880 が入っている
- `NO_PREVIOUS_RUN`: 前月 2026-10 の entities.parquet が無いため差分を計算していない

### p2_candidates

- `SHARED_RUA_DOMAIN_DROPPED` ×5: 複数企業の rua 宛先になっているドメインを候補から落とした。**第三者のレポート処理サービスを企業のドメインとして計測しない。** ベンダー名が分かれば configs/vendors/dmarc_rua_vendors.yaml に、本当にグループ共用なら configs/domains/manual_domains.csv に足すこと
- `DOMAIN_SHARED_BY_ENTITIES` ×15: 同一ドメインが複数企業に紐づいている。持株会社・グループ共用の可能性
- `UNALIGNED_RUA_TARGET` ×243: 自社ドメインでない rua 宛先があった。**候補にしていない。** 第三者のレポート処理サービスであり、その企業の送信ドメインではない。ベンダー名が分かれば configs/vendors/dmarc_rua_vendors.yaml に足すと P6 の dmarc_vendor 推定が埋まる
- `CT_UPSTREAM_UNAVAILABLE`: crt.sh に断られた回数（20）が、答えが返った回数（9）を上回っている。**この月の CT 由来の候補はキャッシュにあった分だけ**で、少ないのは実態ではなく相手が落ちていたためである。crt.sh が戻ってから同じ run_id で流し直すと、キャッシュに積み上がっている分はそのまま使われる
- `ACCEPTANCE_ZERO_CANDIDATES`: 候補ゼロの企業が 7.0% で閾値 5.0% を超えている

### p4_measure

- `RESUME_FOUND_NOTHING`: --resume を指定したが、続きから測れる bronze が無かった。最初から測っている（bronze が消えている可能性がある）
- `WILDCARD_DNS` ×47: 実在しないセレクタに応答したドメイン。何にでも答える DNS のため DKIM の検出結果は偽陽性の可能性がある

### p5_parse

- `DKIM_WILDCARD_SUSPECT` ×47: 対照クエリが応答したドメイン。DKIM の検出は偽陽性の可能性
- `RUA_DOMAIN_UNREGISTERED` ×3: rua の宛先ドメインが未登録。第三者が登録すればレポートが漏洩しうる（Hureau et al., PAM 2024）
- `MTA_STS_POLICY_UNUSABLE` ×22: MTA-STS の TXT はあるが、ポリシーを取れない・読めないドメイン。**送る側の MTA も同じく読めないので、MTA-STS は効いていない**
- `MTA_STS_MX_MISMATCH` ×6: MTA-STS のポリシーの mx に、実際の MX が入っていない。**enforce なら、対応している送り手からのメールが届かない**
- `MX_UNREACHABLE` ×9: どの MX ホストにも宛先が無い（名前が無い、またはアドレスが無い）ドメイン。**このドメイン宛のメールは届かない。** 終わったサービスや綴りの誤りを指したままのものがある

### p6_infer

- `NO_STALE_HISTORY`: 前月の inferences.parquet が無いため stale の連続月数を引き継げない。3 か月連続の判定は今月を1か月目として数え直す
- `UNKNOWN_MX_HOSTS` ×100: 辞書に無い MX ホストが 136 種、351 ドメインぶんある。**一覧に出しているのは上位 100 種まで**で、ほかに 36 種（36 ドメイン）が載っていない。**自社ドメイン配下の MX 329 ドメイン（168 社）は一覧から外している** ── 辞書に足してもその1社にしか効かない。**名前の無い MX ホスト 34 ドメインぶんも外している**（製品ではない。P5 の MX_UNREACHABLE を見ること）。頻度順の上位を手で辞書に追記すると推定率が上がる（DESIGN.md P6 実装メモ）
- `SECURITY_GATEWAY_UNDETECTABLE` ×3257: セキュリティ製品を DNS 上で検出できなかったドメイン。**理由の内訳: api_mode_product 2918件、self_hosted_mx 136件、spf_flattened 203件、tenant_placeholder_mx 1件。** どれも「使っていない」と解釈してはならない

### p7_aggregate

- `COMMON12_UNMAPPED` ×57: 共通12分類が付いていない企業は業種別集計から外した。業種不明を1つの業種として扱うと分母が歪む
- `NO_PREVIOUS_FACTS`: 前月 2026-10 の facts が無いため差分を計算していない
- `GOLD_FROM_DEGRADED_RUN`: P2 は相手が落ちていた（CT_UPSTREAM_UNAVAILABLE）が、既にある gold より分母が小さくならないので書いた。CT 由来の候補はキャッシュにあった分だけである
- `GOLD_OTHER_POPULATIONS_KEPT` ×68: 同じ月の gold にあった他の母集団の行を残した: jp-all-listed

### p8_publish

- `TIER2_DISABLED`: 第2層は無効。個社名付き明細は出力していない。有効化には事前通知から最低 30 日の訂正期間とアクセス制御の設定が必要

