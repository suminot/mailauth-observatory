# Email DNS Monitor

<div class="subtitle">メール認証の月次観測</div>

日本と米国の上場企業について、メール認証（SPF / DKIM / DMARC）と周辺プロトコルの
公開状況を毎月 DNS から観測しています。**標準への準拠状況の計測であり、
各社のセキュリティを総合評価するものではありません。**

```js
const meta = FileAttachment("data/meta.json").json();
const overall = FileAttachment("data/stats_overall.json").json();
```

```js
import { rate, pct, band, BAND_COLORS, checklist, checklistClass, indicators, entityIndicators, num } from "./components/format.js";
import { coverageRing } from "./components/gauge.js";
import { table } from "./components/table.js";
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
// 起点ドメインが取れず、一度も計測に現れなかった企業数。
// **率の分母にはこの企業が入っていない。**伏せると、読み手は
// total_entities 社を測ったと読む（原則5）
const entityGap = Math.max(
  0,
  (current?.total_entities ?? 0) - (current?.entities_with_domains ?? 0)
);
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
      <span class="readout-cap">${entityGap > 0
        ? html`${population}<br><strong>うち ${num(entityGap)} 社は起点が取れず、率に入っていない</strong>`
        : population}</span>
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

```js
// 欠けが無ければ何も出さない。**無い月に「0社欠けています」と書くと、
// 注意書きが背景になって読まれなくなる**
display(
  entityGap > 0
    ? html`<p class="gap-note">起点となる公式サイトが取れなかった
        <strong>${num(entityGap)} 社</strong>は、候補ドメインが1件も無いため
        上の率には含まれていません。率は
        ${num(current?.entities_with_domains)} 社についての観測です。
        <a href="./methodology#起点ドメインが取れなかった企業">測り方</a></p>`
    : html``
);
```

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
display(table(indicators(current ?? {}), { sort: null, rows: 12, width: { 指標: 300, ドメイン: 90, 割合: 80, 補足: 260 } }));
```

棒の長さは傾向を見るのに向きますが、「SPF は何ドメインか」には答えません。
件数と割合を併記しています。**割合の分母はいずれも観測できたドメイン数です。**

### 企業数で見た場合

```js
display(
  table(entityIndicators(current ?? {}), {
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
  table(
    [
      { 区分: "名目 reject（そう書いてある）", ドメイン: current?.nominal_reject_domains },
      { 区分: "実効 reject（pct 無し・t=n・rua 有）", ドメイン: current?.enforced_reject_domains },
      { 区分: "rua が無く可視性がない", ドメイン: current?.blind_reject_domains },
    ],
    { sort: null }
  )
);
```

### 集計レポートの宛先があるか

DMARC の `rua=` は、受け手が送る集計レポートの宛先です。**宛先が無いと、
自分のドメイン名で送られたメールが受け手でどう扱われたかが分かりません。**
`p=none` は「まず観察する」ための設定ですが、宛先が無ければ観察の材料も
届きません。

```js
display(
  current?.dmarc_no_rua_domains == null
    ? html`<p class="muted">この月はまだ集計していません。<strong>0 ではありません。</strong></p>`
    : table(
        [
          { 区分: "DMARC を出している", ドメイン: current?.dmarc_adopted_domains },
          { 区分: "うち rua（集計レポートの宛先）が無い", ドメイン: current?.dmarc_no_rua_domains },
          { 区分: "うち p=none で rua も無い", ドメイン: current?.dmarc_none_no_rua_domains },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- 見ているのは主ドメインの `_dmarc` だけです。レポートを別の仕組み（受け手の
  管理画面など）で見ている会社もあります
- `ruf=`（失敗の個別レポート）の有無は見ていません

</div>

### レポートの宛先が受け取りを認めているか

`rua` に自分のドメインの外の宛先（グループ会社や集計サービス）を書くときは、
**宛先のドメインが「受け取ってよい」というレコード**
（`<ドメイン>._report._dmarc.<宛先>`）を出している必要があります。出ていないと、
受け手はレポートを送らないことがあります ── DMARC を出していても、集計が
届いていない可能性があります。

```js
display(
  current?.rua_external_domains === undefined
    ? html`<p class="muted">この月はまだ集計していません。<strong>0 ではありません。</strong></p>`
    : table(
        [
          { 区分: "rua に外部の宛先がある", ドメイン: current?.rua_external_domains },
          { 区分: "うち宛先が受け取りを認めるレコードを出していない", ドメイン: current?.rua_unauthorized_domains },
          { 区分: "うち宛先のドメインが登録されていない", ドメイン: current?.rua_unregistered_domains },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- 引けなかった宛先は「出していない」に数えていません
- 受け手によっては、このレコードを確かめずに送ることもあります。
  届いていないとまでは言えません
- 宛先のドメインが登録されていない場合、第三者がそのドメインを取ると
  レポートを受け取れてしまいます

</div>

### SPF が DNS を引く回数の上限を超えていないか

SPF で DNS を引く項（`include` `a` `mx` `exists` `redirect` など）は、
**`include` の先の分も合わせて 10 までです**（RFC 7208 §4.6.4）。超えると、
受け手の評価が途中でエラー（PermError）になり、SPF が効かないことがあります。
主ドメインのレコードだけ数えると、`include` を3つ並べたドメインが先で 10 を
超えていても「3」に見えるので、**先まで引いて数えています。**

```js
display(
  current?.spf_lookup_exceeded_domains == null
    ? html`<p class="muted">この月はまだ集計していません。<strong>0 ではありません。</strong></p>`
    : table(
        [
          { 区分: "SPF を出している", ドメイン: current?.spf_adopted_domains },
          { 区分: "うち include の先まで数えて上限 10 を超える", ドメイン: current?.spf_lookup_exceeded_domains },
          { 区分: "うち include・redirect の先に SPF が無い（または2つある）", ドメイン: current?.spf_target_broken_domains },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- 数えたのは、`include` と `redirect` を全部たどった場合の数です。受け手は
  一致した時点で評価を止めるので、**超えていても送信元によっては通ります**
- マクロ（`%{i}` など）を含む先や、引けなかった先があって数えきれない
  ドメインは、どちらにも数えていません（上限を超えたと分かったものだけ数えます）
- `mx` の先の名前の数など、別に決まっている上限は見ていません

</div>

SPF の最後の `all` は、**一覧に無い送信元をどう扱うか**を受け手に伝えます。

```js
display(
  current?.spf_all_fail_domains == null
    ? html`<p class="muted">この月はまだ集計していません。<strong>0 ではありません。</strong></p>`
    : table(
        [
          { 最後の項: "-all", 意味: "一覧に無い送信元は失敗とする", ドメイン: current?.spf_all_fail_domains },
          { 最後の項: "~all", 意味: "一覧に無い送信元は疑わしいとする（softfail）", ドメイン: current?.spf_all_softfail_domains },
          { 最後の項: "?all", 意味: "一覧に無い送信元について何も言わない", ドメイン: current?.spf_all_neutral_domains },
          { 最後の項: "+all（または all）", 意味: "どの送信元も通す", ドメイン: current?.spf_all_pass_domains },
          { 最後の項: "all が無い", 意味: "一覧に無い送信元について何も言わない", ドメイン: current?.spf_all_missing_domains },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- `-all` と `~all` のどちらを選ぶかは、DMARC と組み合わせた運用の考え方に
  よります。この表は数を並べるだけで、優劣は付けていません
- `+all` の SPF は、誰が送っても SPF を通します
- `v=spf1` が2つあるドメインはレコード全体が無効なので、この表に入れていません
- `redirect=` で別のレコードに任せている場合は、**任せた先のレコードの `all`**
  で数えています。任せた先を引けなかったドメインは、この表に入れていません

</div>

### 見落とされやすい設定

次の設定は、どれもメールが届かなくなる・なりすましに使われるといった形で
すぐに表に出るとは限らず、気づかれにくい設定です。

```js
display(
  current?.txt_apex_oversize_domains == null
    ? html`<p class="muted">この月はまだ集計していません。<strong>0 ではありません。</strong></p>`
    : table(
        [
          { 区分: "SPF の include・redirect の先のドメインが登録されていない", ドメイン: current?.spf_dangling_domains },
          { 区分: "主ドメインの TXT の応答が 1,232 バイトを超える（見積り）", ドメイン: current?.txt_apex_oversize_domains },
          { 区分: "SPF が通すと書いた IPv4 アドレスが 65,536 を超える", ドメイン: current?.spf_ip4_broad_domains },
          { 区分: "見つかった DKIM の鍵が Ed25519 だけ", ドメイン: current?.dkim_ed25519_only_domains },
          { 区分: "DMARC に RFC 9989 で無くなったタグ（pct・rf・ri）がある", ドメイン: current?.dmarc_historic_tags_domains },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- **登録されていない先**：SPF が名指ししている先を第三者が登録すると、その
  第三者がこのドメインとして SPF を通るメールを送れます（2024 年に報告された
  SubdoMailing はこの形でした）。登録ドメインの NS が NXDOMAIN で、その上の
  接尾辞は実在すると確かめられたものだけを数えています
- **TXT の大きさ**：所有確認のトークンなどが積もると、主ドメインの TXT の応答が
  UDP で返る目安（DNS Flag Day 2020 の 1,232 バイト）を超え、TCP での引き直しに
  なります。受け手によっては SPF の評価が一時エラーになることがあります。
  数えたのはレコードの中身から見積もった大きさで、実際の応答の大きさではありません
- **広い IP の範囲**：SPF の `ip4:` に広い範囲（クラウドやホスティングの
  範囲など）を書くと、その範囲を使える他人もこのドメインとして SPF を
  通ります。数えたのは主ドメインのレコードに書いた `ip4:` だけで（`include`
  の先は数えない）、65,536 は IPv4 の /16 1つ分です。自社で広い範囲を
  持っている会社もあるので、多いこと自体は誤りではありません
- **Ed25519 だけの DKIM**：Ed25519（RFC 8463）の署名を検証しない受け手が
  あります。RSA の鍵と並べて置くのが勧められる形です。調べたのは、こちらで
  試したセレクタの範囲だけです
- **廃止されたタグ**：`pct=` `rf=` `ri=` は RFC 9989 で無くなりました。
  RFC 7489 に沿って読む受け手には今も効くので、誤りではありません

</div>

## 自分と、その配下

DMARC の `p=` はそのドメイン自身に効きます。**配下のサブドメインに効くのは
`sp=` で、書かれていなければ `p=` を継承します。**

`p=reject; sp=none` と書いてある場合、そのドメイン自身には `reject` が、
**配下のサブドメインには `none` が適用されます。** 会社の実際のメールが
サブドメイン上にあることは珍しくありません。

```js
// **無い列を 0 として見せない**（原則5）。この欄より前に計測した月の
// gold には列そのものが無い。空欄で出すと「該当なし」と読まれる
display(
  current?.sp_enforced_domains === undefined
    ? html`<p class="muted">この月はまだ <code>sp=</code> を集計していません。<strong>0 ではありません。</strong></p>`
    : table(
        [
          { 区分: "自分に効いている（p=）", ドメイン: current?.dmarc_enforced_domains },
          { 区分: "配下にも効いている（sp= または継承）", ドメイン: current?.sp_enforced_domains },
          { 区分: "sp= で配下だけ弱くしている", ドメイン: current?.sp_weaker_domains },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- **ここに出るのは「`sp=` に何と書いてあるか」です。** サブドメインが自分の
  `_dmarc` を持っている場合、`sp=` は継承されないので、この欄は
  そのサブドメインの実態を表しません
- 実際に引いたサブドメインは下の表に別枠で出しています

</div>

### 実際に引いたサブドメイン

メールの痕跡（`rua` の宛先、SPF の `include:`）から見つかったサブドメインの
うち、**自分の MX か自分の `_dmarc` を持っていたもの**です。

```js
display(
  current?.subdomains_measured === undefined
    ? html`<p class="muted">この月はサブドメインを計測していません。<strong>「無かった」ではありません。</strong></p>`
    : table(
        [
          { 区分: "計測したサブドメイン", 数: current?.subdomains_measured },
          { 区分: "うち観測できた", 数: current?.subdomains_observed },
          { 区分: "自分の DMARC を持つ", 数: current?.subdomains_with_own_dmarc },
      { 区分: "上位の sp= を継承している", 数: current?.subdomains_inherited_policy },
          { 区分: "うち quarantine 以上", 数: current?.subdomains_dmarc_enforced },
          { 区分: "サブドメインでメールを扱う企業数", 数: current?.entities_with_subdomain_mail },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- **この数はこのページの他のどの割合の分母にも入っていません。** 混ぜると
  ドメイン数が増えて前月と比べられなくなるため、別枠で数えています
- **見つかった分だけです。** サブドメインの全数を探す方法はありません。
  ここに出ないことは「無い」を意味しません
- 見つける経路は `rua` の宛先と SPF の `include:`、2026-12 の計測からは
  主ドメインの下の `mail.` と `email.` に限っています。それ以外の経路で
  メールを出しているサブドメインは見えません

</div>

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
  table(
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
