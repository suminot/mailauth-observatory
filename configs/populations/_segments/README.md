# 市場区分の対応表

ここに証券コードと市場区分の対応表を置くと、`mailauth view --view jp-prime`
などの区分ビューが使えるようになる。置かなければ区分ビューは
「区分を判定できない」と報告する（総数0を「該当なし」と誤読させないため）。

## 形式

```csv
securities_code,market_segment,source,retrieved
7203,プライム,有価証券報告書 表紙【上場金融商品取引所】,2026-08-01
4689,プライム,有価証券報告書 表紙【上場金融商品取引所】,2026-08-01
9107,スタンダード,有価証券報告書 表紙【上場金融商品取引所】,2026-08-01
```

- `securities_code` は4桁。EDINET の5桁（末尾0）で書いても先頭4桁に正規化される
- `market_segment` は `プライム` / `スタンダード` / `グロース`、
  または `prime` / `standard` / `growth`
- `source` と `retrieved` は出典表記のために使う。省略できるが書くこと

## 使い方

母集団設定の `source.market_filter.segment_map` にパスを書く。

```yaml
source:
  market_filter:
    segment_source: manual_csv
    segment_map: configs/populations/_segments/jp-tse.csv
```

これは**計測対象を絞る設定ではない**。全上場企業を計測したまま、
各社に区分のラベルを付けるだけである。絞るのはビュー（`configs/views/`）の役目。

計測対象そのものを区分で絞りたい場合は `segment_allowlist` を使うが、
月次の比較が難しくなるので勧めない。

## 使ってはいけないソース

**JPX の `data_j.xls`（東証上場銘柄一覧）は使わない。** 非商用であっても
方針として使わない（DESIGN.md 1.4）。`tests/test_compliance.py` が
リポジトリ全体を検査して落とす。

## 作り方の候補

| 方法 | 可否 | 備考 |
|---|---|---|
| 各社の有価証券報告書 表紙【上場金融商品取引所】 | ◯ 本命 | EDINET 由来。PDL1.0 で加工可。XBRL から機械的に作れるが EDINET API v2 の Subscription-Key が要る |
| 各社 IR ページの記載 | ◯ | 手作業。件数が多い |
| Wikidata | ✗ | 実測した。東証上場で証券コードを持つ項目は1件しかなく、区分の項目も存在しない |
| JPX `data_j.xls` | ✗ | 規約により使わない |

有報からの自動生成は将来のスプリントで実装する。それまでは手作業の CSV でよい。
区分が付いた企業だけがビューに現れ、付いていない企業は
`market_segment` が None のまま全体ビューには残る。
