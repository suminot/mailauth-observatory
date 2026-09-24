---
title: Email DNS Monitor
footer: This site measures conformance to published standards and is not an overall security assessment. Data is <a href="/en/terms">CC0</a>.
---

# Email DNS Monitor

<div class="subtitle">Monthly observation of email authentication</div>

Each month, the publicly available DNS records of listed companies in Japan
are observed for email authentication (SPF, DKIM, DMARC) and the protocols
around it. **This site measures conformance to published standards and is not
an overall security assessment.**

```js
const meta = FileAttachment("../data/meta.json").json();
const overall = FileAttachment("../data/stats_overall.json").json();
```

```js
import { rate, pct, band, BAND_COLORS, checklist, indicators, entityIndicators, num } from "../components/format.js";
import { coverageRing } from "../components/gauge.js";
```

```js
const months = meta.months ?? [];
const latest = overall.filter((d) => d.measured_month === `${meta.latest_month}-01`);
const population = view(
  Inputs.select(
    [...new Set(overall.map((d) => d.population_id))],
    { label: "Population", value: latest[0]?.population_id }
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
  <span class="pass">Met</span>
  <span class="attention">Partial</span>
  <span class="absent">Not present</span>
</div>

## Summary

```js
// Only observed counts are shown large. A rate or a completion figure set in
// large type reads as a single summary figure for the whole population,
// which this site does not publish.
display(
  html`<div class="readout-grid">
    <div class="readout">
      <span class="label">Companies</span>
      <span class="value">${num(current?.total_entities)}</span>
      <span class="readout-cap">${population}</span>
    </div>
    <div class="readout">
      <span class="label">Domains measured</span>
      <span class="value">${num(current?.total_domains)}</span>
      <span class="readout-cap">candidates expanded in total</span>
    </div>
    <div class="readout">
      <span class="label">Domains observed</span>
      <span class="value">${num(current?.observed_domains)}</span>
      <span class="readout-cap">the denominator for every rate</span>
    </div>
    <div class="readout">
      <span class="label">Month</span>
      <span class="value">${(current?.measured_month ?? "—").slice(0, 7)}</span>
      <span class="readout-cap">${months.length} month(s) held</span>
    </div>
  </div>`
);
```

```js
display(coverageRing(current?.observed_domains, current?.total_domains, "en"));
```

Rates are calculated **only over domains that could be observed**. Putting
domains that returned SERVFAIL, or nothing at all, into the denominator would
count "could not be observed" as "not configured".

<div class="observed">

### Observed

```js
display(
  Plot.plot({
    marginLeft: 260,
    height: 260,
    x: { domain: [0, 100], percent: true, label: "share of domains observed (%)" },
    y: { label: null },
    marks: [
      Plot.barX(checklist(current ?? {}, "en"), {
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

This is a checklist of standards met. No overall position and no A-to-F style
summary mark is attached. Whether each individual standard is met is meant to
be readable on its own.

```js
display(Inputs.table(indicators(current ?? {}, "en"), { sort: null, rows: 12, width: { Indicator: 300, Domains: 90, Share: 80, Note: 260 } }));
```

Bar length is good for seeing a trend, but it does not answer "how many
domains publish SPF". Counts and shares are given together. **The denominator
for every share is the number of domains observed.**

### Counted by company

```js
display(
  Inputs.table(entityIndicators(current ?? {}, "en"), {
    sort: null,
    width: { Indicator: 300, Companies: 90, Share: 80 },
  })
);
```

Counted by domain alone, companies holding many domains carry more weight. A
company does not necessarily hold exactly one domain, so both are published.

</div>

## Stated versus effective

That a record says `p=reject`, and that the policy actually takes effect, are
two different facts. A `pct` below 100 applies the policy to only part of the
mail, `t=y` means test mode, and without `rua` the operator cannot see what is
being rejected.

```js
display(
  Inputs.table(
    [
      { Category: "Stated reject (that is what it says)", Domains: current?.nominal_reject_domains },
      { Category: "Effective reject (no pct, t=n, rua present)", Domains: current?.enforced_reject_domains },
      { Category: "Enforcing but no rua, so no visibility", Domains: current?.blind_reject_domains },
    ],
    { sort: null }
  )
);
```

## Over time

```js
display(
  months.length < 2
    ? html`<p>A trend will appear once two or more months have been observed. There ${months.length === 1 ? "is" : "are"} currently ${months.length}.</p>`
    : Plot.plot({
        height: 300,
        y: { label: "share of domains observed (%)", percent: true, domain: [0, 100] },
        x: { label: null, type: "band" },
        color: {
          legend: true,
          // The three series are nested (SPF ⊃ DMARC ⊃ enforced), so they are
          // shaded rather than coloured by outcome
          domain: ["SPF", "DMARC", "DMARC enforcing"],
          range: ["var(--series-1)", "var(--series-2)", "var(--series-3)"],
        },
        marks: [
          Plot.lineY(
            series.flatMap((d) => [
              { Month: d.measured_month.slice(0, 7), Indicator: "SPF", Value: rate(d.spf_adopted_domains, d.observed_domains) },
              { Month: d.measured_month.slice(0, 7), Indicator: "DMARC", Value: rate(d.dmarc_adopted_domains, d.observed_domains) },
              { Month: d.measured_month.slice(0, 7), Indicator: "DMARC enforcing", Value: rate(d.dmarc_enforced_domains, d.observed_domains) },
            ]),
            { x: "Month", y: "Value", stroke: "Indicator", marker: true }
          ),
          Plot.ruleY([0]),
        ],
      })
);
```

## Maturity stages

MTA-STS, BIMI and DANE all presuppose DMARC, so simply adding them up would
count the lower items twice. They are shown as an ordered set of stages.

```js
const dist = JSON.parse(current?.maturity_stage_dist ?? "{}");
const STAGE_LABELS = {
  "0": "Stage 0  SPF only / no records",
  "1": "Stage 1  SPF + DKIM + p=none",
  "2": "Stage 2  enforcing policy",
  "3": "Stage 3  + MTA-STS / TLS-RPT + DNSSEC",
  "4": "Stage 4  + BIMI (VMC) or DANE",
};
display(
  Plot.plot({
    marginLeft: 250,
    height: 200,
    x: { label: "domains" },
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

## Domains that do not send

Most of the domains expanded in the candidate set are not used for sending.
A domain with no sending history and nobody watching it can become a starting
point for spoofing. So domains explicitly locked down are shown separately
from domains with nothing configured.

```js
display(
  Inputs.table(
    [
      { Category: "Sending domains (MX present)", Domains: current?.sending_domains,
        Note: `of which enforcing ${pct(rate(current?.sending_enforced, current?.sending_domains))}` },
      { Category: "Null MX + SPF -all + p=reject", Domains: current?.parked_hardened, Note: "the M3AAWG recommended shape" },
      { Category: "Locked down with an enforcing policy", Domains: current?.parked_defended, Note: "" },
      { Category: "Intent not to send, but DMARC is weak", Domains: current?.parked_intentional, Note: "" },
      { Category: "No MX, SPF or DMARC at all", Domains: current?.parked_neglected, Note: "" },
    ],
    { sort: null }
  )
);
```

<div class="limits">

### Limits of this measurement

```js
display(html`<ul>${(meta.detection_limits_en ?? meta.detection_limits ?? []).map((t) => html`<li>${t}</li>`)}</ul>`);
```

Details are on the [Methodology](/en/methodology) page.

</div>
