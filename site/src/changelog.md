# 変更履歴

計測方法や集計定義の変更を記録する。数字が前月と比べて動いたとき、
**実態が変わったのか計測が変わったのか**を区別できるようにするためである。

この一覧は `mailauth changelog` が生成している（2026-11-04T14:57:27+00:00）。
版の変化は機械が検出するが、**それが数字にどう影響したかは人が書く。**
「解釈」の欄が空いている月は、まだ書かれていないということである。

## 2026-11

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
