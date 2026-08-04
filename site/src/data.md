# データ

```js
const meta = FileAttachment("data/meta.json").json();
```

## ライセンス

```js
display(html`<p>集計結果は <strong>${meta.license ?? "CC0-1.0"}</strong> で公開しています。
出典表記なしで自由に使えますが、下記の一次データの出典表記は各提供元の規約に従ってください。</p>`);
```

```js
display(html`<ul>${(meta.attribution ?? []).map((t) => html`<li>${t}</li>`)}</ul>`);
```

## ダウンロード

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

Parquet は月ごとに
[リポジトリの gold/](https://github.com/suminot/mailauth-observatory/tree/main/gold)
に置いてあります。同じ数字を二重に持つと片方だけ古くなるため、
このサイトではコピーせず参照しています。

## 列の意味

主要な列だけ挙げます。全列の定義は
[contracts.py](https://github.com/suminot/mailauth-observatory/blob/main/src/mailauth/contracts.py)
にあります。

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
| `dkim_not_found_domains` | 既知セレクタでは検出できなかった。**未設定ではない** |
| `maturity_stage_dist` | 成熟度ステージ 0〜4 の分布（JSON） |
| `parked_hardened` | Null MX + `-all` + `p=reject` |
| `parked_neglected` | MX / SPF / DMARC のいずれも無い |
| `dane_orphan` | TLSA はあるが DNSSEC 署名が確認できない |
| `delta_prev_month` | 前月との差分（JSON） |

企業数ベースとドメイン数ベースの両方を出しています。ドメイン数だけで見ると、
ドメインを多く持つ企業の重みが大きくなります。

## 個社別の明細について

企業名を伴う明細は、対象企業への事前通知と訂正期間を経てから、認証の内側で
限定公開する方針です。現時点では公開していません。
