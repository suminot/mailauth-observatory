# メール基盤・前段

MX / SPF / DKIM の観測から、メールがどの基盤を通っているかを推し量った数字です。

**推察であって、契約の事実ではありません。** DNS から見える痕跡を辞書と
照合しているだけなので、下の「この数字で言えないこと」を必ず合わせて読んでください。

```js
const platforms = FileAttachment("data/stats_platform.json").json();
const meta = FileAttachment("data/meta.json").json();
```

```js
import { rate, pct } from "./components/format.js";
import { table } from "./components/table.js";
import { vendorLabel } from "./components/vendor.js";
```

```js
const months = (meta.months ?? []).slice().reverse();
const month = view(Inputs.select(months, { label: "月", value: meta.latest_month }));
```

```js
const rows = platforms.filter((d) => d.measured_month === `${month}-01`);
```

## この数字で言えないこと

<div class="limits">

- **観測できなかったものは、この表に出てきません。** 引けなかったドメインは
  「使っていない」ではなく「分からない」です
- **DNS に痕跡を残さない製品があります。** 配送経路に入らず API で連携する
  ものは、原理的にここには出ません
- **MX が自社ドメイン配下の場合、製品名は分かりません。** 運用はしていて、
  こちらから見えないだけです
- **解約後も消え残る設定があります。** 3か月分の観測が揃うまで、残骸と
  現役を見分けられません
- **契約の有無は分かりません。** メールに使っていない契約はここに出ませんし、
  逆に痕跡だけが残っていることもあります

</div>

## 分母は2つあります

```js
const denom = view(
  Inputs.radio(
    new Map([
      ["判明した分のうち", "identified"],
      ["観測できた全体のうち", "observed"],
    ]),
    { label: "割合の分母", value: "identified" }
  )
);
```

`判明した分` は、その層で基盤を同定できたドメインだけを分母にします。
`観測できた全体` は、同定できなかったものも分母に入れます。
**前者だけだと分母が何か伝わらず、後者だけだと採用が少なく見えます。**

```js
function layerTable(layer) {
  const items = rows.filter((d) => d.layer === layer);
  if (!items.length) return html`<p class="muted">この月のデータはありません。</p>`;
  const total = denom === "identified" ? "identified" : "observed";
  return table(
    items
      .slice()
      .sort((a, b) => b.domains_any - a.domains_any)
      .map((d) => ({
        ベンダー: vendorLabel(d.vendor, "ja"),
        "OEM元": d.engine ?? "—",
        企業数: d.entities_any,
        ドメイン数: d.domains_any,
        "ドメインの割合": pct(rate(d.domains_any, d[`${total}_domains`])),
        "うち受信が向いている": d.domains_receiving,
      })),
    { rows: 30 }
  );
}
```

## 実基盤

メールボックスがある場所です。

```js
display(layerTable("platform"));
```

## 受信の前段

MX を握っている製品です。**この後ろに実基盤があります。**

```js
display(layerTable("inbound_gateway"));
```

## 送信の前段

MX を握らず、送信だけを通す製品です（誤送信対策など）。
**`うち受信が向いている` が 0 なのは正常です** ── 受信はしない製品だからです。

```js
display(layerTable("outbound_gateway"));
```

## 読み方の補足

- **同じ企業が3つの層すべてに出ることがあります。** 受信と送信で別の会社を
  使っている例を実際に観測しています
- **同じ層の中では1ドメインにつき1つだけ数えます。** 複数の痕跡があるときは
  証拠の強い方を採ります（両方数えると合計が100%を超えるため）
- **`OEM元` が同じ行は、中身が同じ仕組みです。** 売っている会社は違います
- 5社未満のベンダーは「その他（秘匿）」に束ねています。件数は残しています
