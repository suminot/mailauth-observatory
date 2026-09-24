# 業種別

共通12分類での集計です。1分類あたりの企業数が5社未満になるセルは
「その他（秘匿）」に束ねています。

```js
const sectors = FileAttachment("data/stats_by_sector.json").json();
const meta = FileAttachment("data/meta.json").json();
```

```js
import { rate, pct } from "./components/format.js";
```

```js
const month = view(
  Inputs.select((meta.months ?? []).slice().reverse(), { label: "月", value: meta.latest_month })
);
```

```js
const rows = sectors
  .filter((d) => d.measured_month === `${month}-01`)
  .map((d) => ({
    業種: d.common12_label,
    企業: d.n_entities,
    観測ドメイン: d.observed_domains,
    SPF: pct(rate(d.spf_adopted_domains, d.observed_domains)),
    DMARC: pct(rate(d.dmarc_adopted_domains, d.observed_domains)),
    強制: pct(rate(d.dmarc_enforced_domains, d.observed_domains)),
    "実効 reject": pct(rate(d.enforced_reject_domains, d.observed_domains)),
    秘匿: d.suppressed ? "束ねた" : "",
  }));
display(Inputs.table(rows, { sort: "業種", rows: 20 }));
```

## なぜ12分類までなのか

33業種のまま公開すると、空運業や海運業のように企業数の少ない分類が5社未満に
近づきます。そのため細分軸での公開はしていません。

秘匿は2段階で行っています。5社未満のセルを束ねるだけでは、束ねた対象が
1つだけのときに全体の合計から公開分を引けば値が復元できてしまいます。
そこで秘匿対象が1つになる場合は、次に小さいセルも合わせて束ねています。

## 業種を軸にするときの注意

業種分類は ISIC Rev.4 を経由して12分類に写したものです。**元になる分類は
母集団によって違います** ── 国内は EDINET の33業種、米国は SEC の SIC です。
日米を同じ軸で並べるために、両方を同じ中間分類に寄せています。
写像の対応表は設定として保管しており、どの一次分類がどこに入ったかを
追えるようにしています。

分類が付かなかった企業は業種別集計から外しています。業種不明を1つの業種として
扱うと分母が歪むためです。全社統計の分母には含まれています。
