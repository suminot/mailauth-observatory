# データ

```js
const meta = FileAttachment("data/meta.json").json();
const overall = FileAttachment("data/stats_overall.json").json();
const sectors = FileAttachment("data/stats_by_sector.json").json();
```

```js
import { rate, pct, indicators, entityIndicators } from "./components/format.js";
import { table } from "./components/table.js";
```

観測した数字をこのページで読めます。**ダウンロードしなくても中身が分かる**
ようにしてあります。

```js
const populations = [...new Set(overall.map((d) => d.population_id))];
```

```js
const population = view(
  Inputs.select(populations, { label: "母集団", value: populations[0] })
);
```

```js
// **月は、選んだ母集団に数字がある月だけを並べる。** 全体の最新月を既定に
// すると、その月を測っていない母集団（2026-10 は米国だけ）で「この月の
// データはありません」から始まる
const months = [...new Set(overall.filter((d) => d.population_id === population).map((d) => d.measured_month.slice(0, 7)))].sort().reverse();
const month = view(Inputs.select(months, { label: "月", value: months[0] }));
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
    ? table(indicators(row), { sort: null, rows: 12, width: { 指標: 300, ドメイン: 90, 割合: 80, 補足: 260 } })
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
    ? table(entityIndicators(row), {
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
    ? table(
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
    ? table(sectorRows, { sort: "業種", rows: 20 })
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
    ? table(history, { sort: null, rows: 24 })
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
| `entities_with_domains` | 候補ドメインが1件以上あった企業数。**`total_entities` との差が、起点が取れず計測に現れなかった企業** |
| `excluded_non_operating` | **母集団から外した、事業会社でないもの**（SPAC・ファンド・ETF）の社数。`total_entities` には入っていない。名前と理由は方法論のページに |
| `spf_adopted_domains` | SPF を公開しているドメイン数 |
| `spf_adopted_entities` | SPF を公開している企業数 |
| `dmarc_enforced_domains` | `quarantine` または `reject` |
| `nominal_reject_domains` | `p=reject` と書いてある |
| `enforced_reject_domains` | `pct` 無し・`t=n`・`rua` 有 |
| `blind_reject_domains` | 強制しているが `rua` が無い |
| `sp_enforced_domains` | **配下のサブドメインに効く強度**が `quarantine` 以上。`sp=` が無ければ `p=` を継承する |
| `sp_weaker_domains` | `sp=` が `p=` より弱い。**そのドメイン自身と配下で適用される強度が違う** |
| `subdomains_measured` | **実際に引いたサブドメイン数。** 上のどの割合の分母にも入っていない（別枠） |
| `subdomains_with_own_dmarc` | そのサブドメイン自身が `_dmarc` を持つ。`sp=` を継承しない |
| `subdomains_inherited_policy` | 自分の `_dmarc` が無く、上位の `sp=` を継承していた数 |
| `entities_with_subdomain_mail` | サブドメインでメールを扱っていた企業数 |
| `dkim_detected_domains` | 既知セレクタで検出できた |
| `dkim_not_found_domains` | 既知セレクタでは検出できなかった。**未設定ではない** |
| `mta_sts_domains` | MTA-STS を公開している（`_mta-sts` の TXT がある） |
| `mta_sts_enforce_domains` | ポリシーを HTTPS で取り、`mode: enforce` だった。取っていない月は空 |
| `mta_sts_testing_domains` | 同じく `mode: testing` だった（失敗を報告するだけで、配送は止めない） |
| `mta_sts_unusable_domains` | TXT はあるが、ポリシーを取れない・読めない。**送る側も読めないので効いていない** |
| `mta_sts_mx_mismatch_domains` | ポリシーの `mx` に実際の MX が入っていない |
| `tls_rpt_domains` | TLS-RPT を公開している |
| `rua_external_domains` | DMARC の集計レポートの宛先（`rua=`）に、自分のドメインの外の宛先がある |
| `rua_unauthorized_domains` | そのうち、宛先のドメインが受け取りを認めるレコード（`<ドメイン>._report._dmarc.<宛先>`）を出していない。**受け手がレポートを送らないことがある**。引けなかったものは数えていない |
| `rua_unregistered_domains` | そのうち、宛先のドメインが登録されていないと確かめられた（第三者が登録すればレポートを受け取れる） |
| `spf_lookup_exceeded_domains` | SPF で DNS を引く項が、`include` と `redirect` の先まで数えて上限 10 を超える。数えきれなかったものは数えていない（超えたと分かったものを除く）。先を引いていない月は空 |
| `spf_target_broken_domains` | SPF の `include` か `redirect` の先に SPF が無い、または2つある（受け手の評価がエラーになる）。先を引いていない月は空 |
| `spf_all_fail_domains` | SPF の最後の項が `-all`。この5列は `v=spf1` が2つあるドメインを数えていない。`redirect=` で任せているものは任せた先の `all` で数える |
| `spf_all_softfail_domains` | SPF の最後の項が `~all` |
| `spf_all_neutral_domains` | SPF の最後の項が `?all` |
| `spf_all_pass_domains` | SPF の最後の項が `+all`（修飾子の無い `all` を含む） |
| `spf_all_missing_domains` | SPF に `all` が無い |
| `spf_all_unknown_domains` | `redirect=` の先を引けず、最後に効く `all` が分からない（上の5列のどれにも入れていない） |
| `spf_dangling_domains` | SPF の `include` か `redirect` の先のうち SPF の無いものについて、登録ドメインが登録されていないと確かめられた（第三者が登録すれば SPF を通るメールを送れる）。引けなかった先があるものは数えていない。列の無い月は空 |
| `txt_apex_oversize_domains` | 主ドメインの TXT の応答の大きさの見積りが 1,232 バイトを超える。実際の応答の大きさではない。列の無い月は空 |
| `spf_ip4_broad_domains` | 主ドメインの SPF が通すと書いた IPv4 アドレス（`ip4:`、重なりは1つに数える、`include` の先は数えない）が 65,536（/16 1つ分）を超える。列の無い月は空 |
| `rua_placeholder_domains` | DMARC の rua の宛先に、例示用に予約された名前（`example.com` など、RFC 2606・6761）がある |
| `reject_with_rua_domains` | `p=reject` で、rua の宛先がある（例示用の名前は除く） |
| `dmarc_np_domains` | DMARC に正しい値の `np=`（存在しないサブドメインへの方針、RFC 9989）がある |
| `dmarc_testing_domains` | DMARC に `t=y`（試行中、RFC 9989）がある |
| `bulk_spf_ok_domains` | 送信しているドメイン（`sending_domains`）のうち、SPF があり評価がエラーにならない（`v=spf1` が1つ、`include` の先まで数えて上限 10 以内） |
| `bulk_dkim_domains` | 同じく、DKIM の鍵が見つかった（試したセレクタの範囲） |
| `bulk_dmarc_ok_domains` | 同じく、DMARC があり、レコードが有効（`p=none` を含む） |
| `bulk_ready_domains` | 同じく、上の3つがそろっている（大口送信者の要件のうち DNS で見られる部分。整合などは見ていない） |
| `dkim_ed25519_only_domains` | 見つかった DKIM の鍵（取り消されたものとワイルドカードを除く）が Ed25519 だけ。列の無い月は空 |
| `dmarc_historic_tags_domains` | DMARC に RFC 9989 で無くなったタグ（`pct=` `rf=` `ri=`）がある。誤りではない。列の無い月は空 |
| `dmarc_invalid_domains` | DMARC を公開しているが、レコードが無効（`v=DMARC1` が2つある、`p=` が無いか不正）。`dmarc_adopted_domains` の内数 |
| `dkim_key_2048_domains` | DKIM を検出したドメインのうち、見つかった RSA 鍵がすべて 2048 ビット以上 |
| `dkim_key_1024_domains` | 同じく、最も短い RSA 鍵が 1024 ビット |
| `dkim_key_short_domains` | 同じく、最も短い RSA 鍵が 1024 ビット未満（RFC 8301 は受け手がこの鍵の署名を有効と見なしてはならないとする） |
| `dmarc_no_rua_domains` | DMARC を公開しているが、集計レポートの宛先（`rua=`）が無い |
| `dmarc_none_no_rua_domains` | そのうち `p=none` |
| `bimi_domains` | BIMI を公開している |
| `bimi_vmc_domains` | そのうち、証明書（VMC など）の場所（`a=`）を書いている。証明書が有効かは見ていない |
| `dnssec_domains` | DNSSEC で署名されている |
| `dane_domains` | TLSA レコードがある |
| `dane_orphan` | TLSA はあるが DNSSEC 署名が確認できない |
| `maturity_stage_dist` | 成熟度ステージ 0〜4 の分布（JSON） |
| `parked_hardened` | Null MX + `-all` + `p=reject` |
| `parked_neglected` | MX / SPF / DMARC のいずれも無い |
| `delta_prev_month` | 前月との差分（JSON）。`policy_transitions` は両方の月に観測できた同じドメインの DMARC の実効の方針の移り変わり（`"none>reject"` の形）、`rua_added` `rua_removed` は rua を足した・外した数 |

企業数ベースとドメイン数ベースの両方を出しています。ドメイン数だけで見ると、
ドメインを多く持つ企業の重みが大きくなります。

## 個社別の明細について

企業名を伴う明細は認証の内側にしか置きません。**現時点では出していません。**
