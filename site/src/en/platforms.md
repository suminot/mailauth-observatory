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
import { rate, pct, populationLabel } from "../components/format.js";
import { table } from "../components/table.js";
import { vendorLabel } from "../components/vendor.js";
```

```js
// **Filter by population.** Mixing countries into one table lists the same
// vendor twice and puts rows with different denominators side by side.
const populations = [...new Set(platforms.map((d) => d.population_id))];
const population = view(
  Inputs.select(populations, { label: "Population", value: populations[0], format: (d) => populationLabel(d, "en") })
);
```

```js
// **Offer only the months this population was measured in.** Defaulting to
// the latest month overall opens on "No data" for a population that was not
// measured that month (2026-10 covers the US only)
const months = [...new Set(platforms.filter((d) => d.population_id === population).map((d) => d.measured_month.slice(0, 7)))].sort().reverse();
const month = view(Inputs.select(months, { label: "Month", value: months[0] }));
```

```js
const rows = platforms.filter(
  (d) => d.population_id === population && d.measured_month === `${month}-01`
);
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
- **An SPF entry is a declaration that a product may send.** It does not show
  that all outgoing mail passes through that product

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
function layerTable(layer, emptyText) {
  const items = rows.filter((d) => d.layer === layer);
  if (!items.length)
    return html`<p class="muted">${emptyText ?? "No data for this month."}</p>`;
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

Products that outgoing mail passes through. We count a product when the SPF
record contains a name that the vendor's own documentation tells customers to
publish for sending. This covers send-only products, such as misdelivery
controls, and inbound front-end products that also relay outgoing mail (those
also appear in the inbound table). **A zero under `MX points here` is
expected** — this layer is counted from SPF traces, not from MX.

An SPF entry declares that the product may send for the domain; **it does not
show that all outgoing mail goes through it.**

```js
display(layerTable("outbound_gateway"));
```

## Services used for sending

```js
// Counted from the 2026-11 re-aggregation; earlier months have no rows
const NEW_SECTION = "This section is counted from the 2026-11 re-aggregation onward; this month has no table.";
```

Mail delivery services and business systems that send notifications —
**services that appear in SPF or DKIM so that mail can be sent from the
company's domain.**

- Only names the service itself tells customers to publish, in **SPF
  `include:` or a DKIM CNAME**, are counted. Domain-ownership TXT records alone
  are left out, since they are not evidence of sending
- **One domain can use several services, and each is counted.** The shares can
  add up to more than 100%
- An SPF entry declares that a service may send; it does not show that it
  does. DKIM covers only what was found among known selectors
- `MX points here` is 0 in this table (MX is not looked at)

```js
display(layerTable("sending_service", NEW_SECTION));
```

## DMARC report aggregation services

Domains whose DMARC aggregate report address (`rua=`) points at a service that
processes reports.

- Only addresses whose domain identifies a service are counted. The company's
  own addresses, and addresses that cannot be identified, are not included
- One domain can list several addresses, and each is counted
- Listing an address does not show a contract with that service

```js
display(layerTable("dmarc_report_service", NEW_SECTION));
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
