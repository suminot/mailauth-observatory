---
title: Mail platforms
footer: This site measures conformance to published standards and is not an overall security assessment. Data is <a href="/en/terms">CC0</a>.
---

# Mail platforms and front-ends

Inferred from observed MX, SPF and DKIM records: which platform the mail
appears to pass through.

**These are inferences, not contractual facts.** They come from matching
traces visible in DNS against a dictionary, so read them together with the
limits below.

```js
const platforms = FileAttachment("../data/stats_platform.json").json();
const meta = FileAttachment("../data/meta.json").json();
```

```js
import { rate, pct } from "../components/format.js";
import { table } from "../components/table.js";
import { vendorLabel } from "../components/vendor.js";
```

```js
const months = (meta.months ?? []).slice().reverse();
const month = view(Inputs.select(months, { label: "Month", value: meta.latest_month }));
```

```js
const rows = platforms.filter((d) => d.measured_month === `${month}-01`);
```

## What these figures cannot tell you

<div class="limits">

- **What could not be observed does not appear here.** A domain we could not
  resolve is unknown, not unused
- **Some products leave no trace in DNS.** Anything that connects over an API
  instead of sitting in the delivery path cannot appear here at all
- **When MX points inside the company's own domain, the product is unknown.**
  Mail is being handled; we simply cannot see by what
- **Settings outlive the contract.** Until three months of observations
  accumulate, leftovers cannot be told apart from current use
- **Contracts are not visible.** A tenant used for something other than mail
  will not appear, and a trace may remain after the service was dropped

</div>

## There are two denominators

```js
const denom = view(
  Inputs.radio(
    new Map([
      ["Of those identified", "identified"],
      ["Of all observed", "observed"],
    ]),
    { label: "Denominator", value: "identified" }
  )
);
```

`Of those identified` counts only the domains whose platform could be named in
that layer. `Of all observed` includes the ones that could not be named.
**The first alone hides what the denominator is; the second alone makes
adoption look smaller than it is.**

```js
function layerTable(layer) {
  const items = rows.filter((d) => d.layer === layer);
  if (!items.length) return html`<p class="muted">No data for this month.</p>`;
  const total = denom === "identified" ? "identified" : "observed";
  return table(
    items
      .slice()
      .sort((a, b) => b.domains_any - a.domains_any)
      .map((d) => ({
        Vendor: vendorLabel(d.vendor, "en"),
        "OEM of": d.engine ?? "—",
        Companies: d.entities_any,
        Domains: d.domains_any,
        "Share of domains": pct(rate(d.domains_any, d[`${total}_domains`])),
        "MX points here": d.domains_receiving,
      })),
    { rows: 30 }
  );
}
```

## Platform

Where the mailboxes are.

```js
display(layerTable("platform"));
```

## Inbound front-end

Products that hold the MX record. **A platform sits behind them.**

```js
display(layerTable("inbound_gateway"));
```

## Outbound front-end

Products that do not hold MX and only handle outgoing mail, such as
misdelivery controls. **A zero under `MX points here` is expected** — these
products do not receive.

```js
display(layerTable("outbound_gateway"));
```

## Notes on reading this

- **One company can appear in all three layers.** We have observed companies
  using different vendors for inbound and outbound mail
- **Within one layer a domain is counted once.** Where several traces appear,
  the stronger evidence wins, because counting both would push the total past
  100%
- **Rows sharing an `OEM of` value run the same engine.** The companies
  selling them differ
- Vendors with fewer than five companies are folded into "Other (suppressed)".
  The counts are kept
