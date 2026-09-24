---
title: サンプル（作り物の数字）
---

<div class="sample-banner" role="note">
  <strong>これはサンプルです。</strong>
  数字はすべて作り物で、実際に観測したものではありません。
  計測が一巡したときに表紙がどう見えるかを確かめるためのページです。
  実際の数字は<a href="/">表紙</a>にあります。
</div>

# Email DNS Monitor

<div class="subtitle">メール認証の月次観測</div>

日本の上場企業について、メール認証（SPF / DKIM / DMARC）と周辺プロトコルの
公開状況を毎月 DNS から観測しています。**標準への準拠状況の計測であり、
各社のセキュリティを総合評価するものではありません。**

```js
const meta = FileAttachment("sample/meta.json").json();
const overall = FileAttachment("sample/stats_overall.json").json();
```

```js
import { rate, pct, band, BAND_COLORS, checklist, checklistClass, indicators, entityIndicators, num } from "./components/format.js";
import { coverageRing } from "./components/gauge.js";
```

```js
const months = meta.months ?? [];
const latest = overall.filter((d) => d.measured_month === `${meta.latest_month}-01`);
const population = view(
  Inputs.select(
    [...new Set(overall.map((d) => d.population_id))],
    { label: "母集団", value: latest[0]?.population_id }
  )
);
```

```js
const series = overall
  .filter((d) => d.population_id === population)
  .sort((a, b) => a.measured_month.localeCompare(b.measured_month));
const current = series.at(-1);
```

<div class="legend">
  <span class="pass">合格</span>
  <span class="attention">要改善</span>
  <span class="absent">未対応</span>
</div>

## 総括

```js
// 大きく出すのは**観測した件数だけ**にする。率や達成度を大書きすると
// 総合スコアのように読まれる（DESIGN.md P8 で禁じている）
display(
  html`<div class="readout-grid">
    <div class="readout">
      <span class="label">企業</span>
      <span class="value">${num(current?.total_entities)}<span class="unit">社</span></span>
      <span class="readout-cap">${population}</span>
    </div>
    <div class="readout">
      <span class="label">計測対象ドメイン</span>
      <span class="value">${num(current?.total_domains)}</span>
      <span class="readout-cap">候補として展開した総数</span>
    </div>
    <div class="readout">
      <span class="label">観測できたドメイン</span>
      <span class="value">${num(current?.observed_domains)}</span>
      <span class="readout-cap">率の分母はこれを使う</span>
    </div>
    <div class="readout">
      <span class="label">観測月</span>
      <span class="value">${(current?.measured_month ?? "—").slice(0, 7)}</span>
      <span class="readout-cap">${months.length} か月分を保持</span>
    </div>
  </div>`
);
```

```js
// 観測できた割合。**達成度ではなく、この計測がどれだけ届いたか。**
// 届かなかった分を伏せると、率をどれだけ信じてよいかが読み手に伝わらない
display(coverageRing(current?.observed_domains, current?.total_domains));
```

率の分母には**観測できたドメインだけ**を使っています。SERVFAIL などで
何も取れなかったドメインを分母に入れると、「取れなかった」が「未対応」として
集計されてしまうためです。

<div class="observed">

### 観測値

```js
display(
  Plot.plot({
    marginLeft: 260,
    height: 260,
    x: { domain: [0, 100], percent: true, label: "観測できたドメインに対する割合 (%)" },
    y: { label: null },
    marks: [
      Plot.barX(checklist(current ?? {}), {
        x: "value",
        y: "label",
        fill: (d) => BAND_COLORS[band(d.value)],
        sort: null,
      }),
      Plot.ruleX([0]),
    ],
  })
);
```

達成した標準のチェックリストとして提示しています。総合順位や A〜F のような
まとめの記号は付けません。個々の標準について満たしているかどうかが、
そのまま読み取れる形にしています。

```js
display(Inputs.table(indicators(current ?? {}), { sort: null, rows: 12, width: { 指標: 300, ドメイン: 90, 割合: 80, 補足: 260 } }));
```

棒の長さは傾向を見るのに向きますが、「SPF は何ドメインか」には答えません。
件数と割合を併記しています。**割合の分母はいずれも観測できたドメイン数です。**

### 企業数で見た場合

```js
display(
  Inputs.table(entityIndicators(current ?? {}), {
    sort: null,
    width: { 指標: 300, 企業: 90, 割合: 80 },
  })
);
```

ドメイン数だけで見ると、ドメインを多く持つ企業の重みが大きくなります。
1社が1ドメインしか持たないとは限らないため、両方を出しています。

</div>

## 名目と実効

`p=reject` と書いてあることと、それが実際に効いていることは別の事実です。
`pct` が 100 未満なら一部にしか適用されず、`t=y` はテストモード、
`rua` が無ければ何が拒否されているかを運用者が確認できません。

```js
display(
  Inputs.table(
    [
      { 区分: "名目 reject（そう書いてある）", ドメイン: current?.nominal_reject_domains },
      { 区分: "実効 reject（pct 無し・t=n・rua 有）", ドメイン: current?.enforced_reject_domains },
      { 区分: "rua が無く可視性がない", ドメイン: current?.blind_reject_domains },
    ],
    { sort: null }
  )
);
```

## 時系列

```js
display(
  months.length < 2
    ? html`<p>複数月の観測が揃うと推移を表示します。現在は ${months.length} か月分です。</p>`
    : Plot.plot({
        height: 300,
        y: { label: "観測できたドメインに対する割合 (%)", percent: true, domain: [0, 100] },
        x: { label: null, type: "band" },
        color: {
          legend: true,
          // **入れ子の関係を濃淡で示す。** 合否の配色を使うと「広い集合ほど
          // 合格」に見え、Plot の既定（赤・青・橙）だと、このサイトが
          // 「未対応」の意味で使っている赤が一番達成している指標に付く
          domain: ["SPF", "DMARC", "DMARC 強制"],
          range: ["var(--series-1)", "var(--series-2)", "var(--series-3)"],
        },
        marks: [
          Plot.lineY(
            series.flatMap((d) => [
              { 月: d.measured_month.slice(0, 7), 指標: "SPF", 値: rate(d.spf_adopted_domains, d.observed_domains) },
              { 月: d.measured_month.slice(0, 7), 指標: "DMARC", 値: rate(d.dmarc_adopted_domains, d.observed_domains) },
              { 月: d.measured_month.slice(0, 7), 指標: "DMARC 強制", 値: rate(d.dmarc_enforced_domains, d.observed_domains) },
            ]),
            { x: "月", y: "値", stroke: "指標", marker: true }
          ),
          Plot.ruleY([0]),
        ],
      })
);
```

## 成熟度ステージ

MTA-STS / BIMI / DANE はいずれも DMARC を前提とするため、単純に足し合わせると
下位の項目を二重に数えてしまいます。順序性のある階層で示しています。

```js
const dist = JSON.parse(current?.maturity_stage_dist ?? "{}");
const STAGE_LABELS = {
  "0": "Stage 0 SPF のみ / レコードなし",
  "1": "Stage 1 SPF + DKIM + p=none",
  "2": "Stage 2 強制ポリシー",
  "3": "Stage 3 + MTA-STS / TLS-RPT + DNSSEC",
  "4": "Stage 4 + BIMI(VMC) または DANE",
};
display(
  Plot.plot({
    marginLeft: 250,
    height: 200,
    x: { label: "ドメイン数" },
    y: { label: null },
    marks: [
      Plot.barX(
        Object.entries(dist).map(([k, v]) => ({ stage: STAGE_LABELS[k] ?? k, n: v })),
        { x: "n", y: "stage", fill: "var(--neutral)", sort: null }
      ),
      Plot.ruleX([0]),
    ],
  })
);
```

## 送信していないドメインの扱い

網羅的に展開したドメインの大半は送信に使われていません。送信実績がなく
監視もされていないドメインは、なりすましの出発点になりえます。そこで
「明示的に固められたもの」と「設定が無いもの」を分けて示しています。

```js
display(
  Inputs.table(
    [
      { 分類: "送信ドメイン（MX あり）", ドメイン: current?.sending_domains,
        補足: `うち強制ポリシー ${pct(rate(current?.sending_enforced, current?.sending_domains))}` },
      { 分類: "Null MX + SPF -all + p=reject", ドメイン: current?.parked_hardened, 補足: "M3AAWG の推奨構成" },
      { 分類: "強制ポリシーで固めている", ドメイン: current?.parked_defended, 補足: "" },
      { 分類: "送信禁止の意図はあるが DMARC が弱い", ドメイン: current?.parked_intentional, 補足: "" },
      { 分類: "MX / SPF / DMARC のいずれも無い", ドメイン: current?.parked_neglected, 補足: "" },
    ],
    { sort: null }
  )
);
```

<div class="limits">

### この計測の限界

```js
display(html`<ul>${(meta.detection_limits ?? []).map((t) => html`<li>${t}</li>`)}</ul>`);
```

詳細は[方法論](/methodology)に記載しています。

</div>
