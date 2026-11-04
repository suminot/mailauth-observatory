# 変更履歴

計測方法や集計定義の変更を記録する。数字が前月と比べて動いたとき、
**実態が変わったのか計測が変わったのか**を区別できるようにするためである。

この一覧は `mailauth changelog` が生成している（2026-11-04T19:48:21+00:00）。
版の変化は機械が検出するが、**それが数字にどう影響したかは人が書く。**
「解釈」の欄が空いている月は、まだ書かれていないということである。

## 2026-11

### この月の計測方法の変更（人が書いたもの）

- 送信の前段の判定に、Proofpoint・Mimecast・Barracuda・Cisco・Trend Micro などが送信のために SPF に載せる名前を足した（各社の資料にあるものだけ）。米国の送信の前段の企業数はこれで大きく増えている。
- DKIM の CNAME の行き先（SendGrid・HubSpot・Constant Contact・Salesforce など）と、SPF の include（KnowBe4・Mailgun・Oracle・国内の配信サービスなど）の判定を足した。配信サービスの企業数の増加はこれによる。
- 層を持たない推定（配信・DMARC 集計）を「配信に使われているサービス」「DMARC レポートの集計サービス」に分けた。ドメインの所有確認の TXT だけのものは送信の証拠として数えないようにした。
- DMARC の集計レポートの宛先が受け取りを認めるレコードを出しているかを、集計に足した。
- 実基盤の判定に、運営会社の資料で確かめた MX（iCloud+・Seznam・Daum など）を足した。
- SPF で DNS を引く項の数を、include と redirect の先まで引いて数えるようにした。上限 10 を超えるドメインと、先に SPF が無いドメインを集計に足した。
- SPF の最後の `all` の修飾子（-all・~all・?all・+all・無し）ごとの数を集計に足した。
- DMARC を公開しているが、レコードが無効（2つある・p= が無いか不正）なドメインの数を、公開しているの内数として足した。
- DKIM を検出したドメインを、見つかった RSA 鍵のうち最も短いものの長さ（2048 以上・1024・1024 未満）で分けて数えるようにした。Ed25519 の鍵は長さを見積もらないようにした。

計測側の変更はない。

### 主要指標の増減

| 母集団 | 指標 | 前月 | 当月 | 差 |
|---|---|---|---|---|
| us-all-listed | total_entities | 5267 | 5254 | -13 |
| us-all-listed | total_domains | 4877 | 10753 | +5876 |
| us-all-listed | observed_domains | 4877 | 10753 | +5876 |
| us-all-listed | entities_with_domains | 4869 | 5163 | +294 |
| us-all-listed | spf_adopted_domains | 4616 | 6814 | +2198 |
| us-all-listed | dmarc_adopted_domains | 4156 | 6198 | +2042 |
| us-all-listed | dmarc_enforced_domains | 3087 | 4742 | +1655 |
| us-all-listed | enforced_reject_domains | 1690 | 2949 | +1259 |
| us-all-listed | dkim_detected_domains | 3748 | 4601 | +853 |
| us-all-listed | parked_hardened | 4 | 63 | +59 |
| us-all-listed | parked_neglected | 140 | 3337 | +3197 |

- 前月 2026-10 の manifest が無いため、計測側の変更を検出していない。初回の月か、実行記録が失われている

## 2026-10

計測側の変更はない。

- 前月 2026-09 の manifest が無いため、計測側の変更を検出していない。初回の月か、実行記録が失われている

## 2026-09

計測側の変更はない。

- 前月 2026-08 の manifest が無いため、計測側の変更を検出していない。初回の月か、実行記録が失われている

## 記録の方針

集計定義を変えた場合、その月の数字と前月の数字は直接比較できない。
過去の集計は遡って作り直さず、履歴として残す。
