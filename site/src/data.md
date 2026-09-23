# データ

```js
const meta = FileAttachment("data/meta.json").json();
const overall = FileAttachment("data/stats_overall.json").json();
const sectors = FileAttachment("data/stats_by_sector.json").json();
```

```js
import { rate, pct, indicators, entityIndicators } from "./components/format.js";
```

観測した数字をこのページで読めます。**ダウンロードしなくても中身が分かる**
ようにしてあります。

```js
const months = (meta.months ?? []).slice().reverse();
const populations = [...new Set(overall.map((d) => d.population_id))];
```

```js
const population = view(
  Inputs.select(populations, { label: "母集団", value: populations[0] })
);
```

```js
const month = view(Inputs.select(months, { label: "月", value: meta.latest_month }));
```

```js
const row = overall.find(
  (d) => d.population_id === population && d.measured_month === `${month}-01`
);
```

```js
display(
  row
    ? html`<p>観測できたドメイン <strong>${row.observed_domains}</strong> /
        計測対象 ${row.total_domains}（企業 ${row.total_entities} 社）</p>`
    : html`<p>この母集団と月の観測はまだありません。</p>`
);
```

## 標準ごとの状況

```js
display(
  row
    ? Inputs.table(indicators(row), { sort: null, rows: 12, width: { 指標: 300, ドメイン: 90, 割合: 80, 補足: 260 } })
    : html`<p>観測が入ると表示します。</p>`
);
```

**割合の分母はいずれも観測できたドメイン数です。** 計測対象の総数ではありません。
SERVFAIL などで何も取れなかったドメインを分母に入れると、「取れなかった」が
「未対応」として集計されてしまいます。

## 企業数で見た場合

```js
display(
  row
    ? Inputs.table(entityIndicators(row), {
        sort: null,
        width: { 指標: 300, 企業: 90, 割合: 80 },
      })
    : html`<p>—</p>`
);
```

## 名目と実効

```js
display(
  row
    ? Inputs.table(
        [
          { 区分: "名目 reject（そう書いてある）", ドメイン: row.nominal_reject_domains },
          { 区分: "実効 reject（pct 無し・t=n・rua 有）", ドメイン: row.enforced_reject_domains },
          { 区分: "rua が無く可視性がない", ドメイン: row.blind_reject_domains },
        ],
        { sort: null }
      )
    : html`<p>—</p>`
);
```

## 業種別

```js
const sectorRows = sectors
  .filter((d) => d.measured_month === `${month}-01` && d.population_id === population)
  .map((d) => ({
    業種: d.common12_label,
    企業: d.n_entities,
    観測ドメイン: d.observed_domains,
    SPF: pct(rate(d.spf_adopted_domains, d.observed_domains)),
    DMARC: pct(rate(d.dmarc_adopted_domains, d.observed_domains)),
    強制: pct(rate(d.dmarc_enforced_domains, d.observed_domains)),
    秘匿: d.suppressed ? "束ねた" : "",
  }));
display(
  sectorRows.length
    ? Inputs.table(sectorRows, { sort: "業種", rows: 20 })
    : html`<p>この月の業種別集計はまだありません。</p>`
);
```

## 月ごとの推移

```js
const history = overall
  .filter((d) => d.population_id === population)
  .sort((a, b) => b.measured_month.localeCompare(a.measured_month))
  .map((d) => ({
    月: d.measured_month.slice(0, 7),
    観測ドメイン: d.observed_domains,
    SPF: pct(rate(d.spf_adopted_domains, d.observed_domains)),
    DKIM: pct(rate(d.dkim_detected_domains, d.observed_domains)),
    DMARC: pct(rate(d.dmarc_adopted_domains, d.observed_domains)),
    強制: pct(rate(d.dmarc_enforced_domains, d.observed_domains)),
    "実効 reject": pct(rate(d.enforced_reject_domains, d.observed_domains)),
    DNSSEC: pct(rate(d.dnssec_domains, d.observed_domains)),
  }));
display(
  history.length
    ? Inputs.table(history, { sort: null, rows: 24 })
    : html`<p>観測が入ると表示します。</p>`
);
```

**月をまたぐ比較には注意が要ります。** 母集団の構成や辞書が変わると、
実態が変わっていなくても数字が動きます。何が変わったかは[変更履歴](/changelog)に
残しています。

## ライセンス

```js
display(html`<p>集計結果は <strong>${meta.license ?? "CC0-1.0"}</strong> で公開しています。
出典表記なしで自由に使えますが、下記の一次データの出典表記は各提供元の規約に従ってください。</p>`);
```

```js
display(html`<ul>${(meta.attribution ?? []).map((t) => html`<li>${t}</li>`)}</ul>`);
```

## ダウンロード

上の表と同じ数字です。手元で加工したい場合に使ってください。

```js
display(
  html`<ul>
    <li><a href="data/stats_overall.json">stats_overall.json</a> ── 全社統計（全月分）</li>
    <li><a href="data/stats_overall.csv">stats_overall.csv</a></li>
    <li><a href="data/stats_by_sector.json">stats_by_sector.json</a> ── 業種別集計（全月分）</li>
    <li><a href="data/stats_by_sector.csv">stats_by_sector.csv</a></li>
    <li><a href="data/meta.json">meta.json</a> ── 月の一覧、ライセンス、計測の限界</li>
  </ul>`
);
```

Parquet は月ごとに保管しています。同じ数字を二重に持つと片方だけ古くなるため、
このサイトではコピーせず参照しています。

## 列の意味

上の表で使っている主要な列です。

| 列 | 意味 |
|---|---|
| `total_domains` | 計測対象にしたドメイン数 |
| `observed_domains` | **うち観測できた数。率の分母はこれを使う** |
| `spf_adopted_domains` | SPF を公開しているドメイン数 |
| `spf_adopted_entities` | SPF を公開している企業数 |
| `dmarc_enforced_domains` | `quarantine` または `reject` |
| `nominal_reject_domains` | `p=reject` と書いてある |
| `enforced_reject_domains` | `pct` 無し・`t=n`・`rua` 有 |
| `blind_reject_domains` | 強制しているが `rua` が無い |
| `dkim_detected_domains` | 既知セレクタで検出できた |
| `dkim_not_found_domains` | 既知セレクタでは検出できなかった。**未設定ではない** |
| `mta_sts_domains` | MTA-STS を公開している |
| `tls_rpt_domains` | TLS-RPT を公開している |
| `bimi_domains` | BIMI を公開している |
| `dnssec_domains` | DNSSEC で署名されている |
| `dane_domains` | TLSA レコードがある |
| `dane_orphan` | TLSA はあるが DNSSEC 署名が確認できない |
| `maturity_stage_dist` | 成熟度ステージ 0〜4 の分布（JSON） |
| `parked_hardened` | Null MX + `-all` + `p=reject` |
| `parked_neglected` | MX / SPF / DMARC のいずれも無い |
| `delta_prev_month` | 前月との差分（JSON） |

企業数ベースとドメイン数ベースの両方を出しています。ドメイン数だけで見ると、
ドメインを多く持つ企業の重みが大きくなります。

## 個社別の明細について

企業名を伴う明細は認証の内側にしか置きません。**現時点では出していません。**
