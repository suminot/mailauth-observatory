---
title: By sector
footer: This site measures conformance to published standards and is not an overall security assessment. Data is <a href="/en/terms">CC0</a>.
---

# By sector

Aggregated over the twelve common sectors. Any cell holding fewer than five
companies is folded into "Other (suppressed)".

```js
const sectors = FileAttachment("../data/stats_by_sector.json").json();
const meta = FileAttachment("../data/meta.json").json();
```

```js
import { rate, pct } from "../components/format.js";
```

```js
const month = view(
  Inputs.select((meta.months ?? []).slice().reverse(), { label: "Month", value: meta.latest_month })
);
```

```js
const rows = sectors
  .filter((d) => d.measured_month === `${month}-01`)
  .map((d) => ({
    Sector: d.common12_label,
    Companies: d.n_entities,
    Observed: d.observed_domains,
    SPF: pct(rate(d.spf_adopted_domains, d.observed_domains)),
    DMARC: pct(rate(d.dmarc_adopted_domains, d.observed_domains)),
    Enforced: pct(rate(d.dmarc_enforced_domains, d.observed_domains)),
    "Effective reject": pct(rate(d.enforced_reject_domains, d.observed_domains)),
    Suppressed: d.suppressed ? "folded" : "",
  }));
display(
  rows.length
    ? Inputs.table(rows, { sort: "Sector", rows: 20 })
    : html`<p>There is no sector breakdown for this month yet.</p>`
);
```

**The denominator for every rate is the number of domains that could be
observed**, not the number of domains measured.

## Why only twelve sectors

Published at the original 33 sectors, thinly populated ones such as air
transport or marine transport come close to fewer than five companies, so no
finer axis is published.

Suppression is applied in two steps. Folding cells with fewer than five
companies is not enough on its own: if only one cell ends up folded, its
value can be recovered by subtracting the published cells from the total. So
when suppression would leave a single folded cell, the next smallest cell is
folded with it.

## Reading the sector axis

The sector classification maps EDINET's 33 sectors through ISIC Rev.4 into
twelve. The mapping table is kept as configuration, so it stays possible to
trace which primary sector went where.

Companies with no sector assigned are left out of the sector aggregation.
Treating "sector unknown" as a sector of its own would distort the
denominator. They remain in the overall statistics.
