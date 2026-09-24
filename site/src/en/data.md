---
title: Data
footer: This site measures conformance to published standards and is not an overall security assessment. Data is <a href="/en/terms">CC0</a>.
---

# Data

```js
const meta = FileAttachment("../data/meta.json").json();
const overall = FileAttachment("../data/stats_overall.json").json();
const sectors = FileAttachment("../data/stats_by_sector.json").json();
```

```js
import { rate, pct, indicators, entityIndicators } from "../components/format.js";
```

The observed figures can be read on this page. **You should not have to
download anything to see what is in the data.**

```js
const months = (meta.months ?? []).slice().reverse();
const populations = [...new Set(overall.map((d) => d.population_id))];
```

```js
const population = view(
  Inputs.select(populations, { label: "Population", value: populations[0] })
);
```

```js
const month = view(Inputs.select(months, { label: "Month", value: meta.latest_month }));
```

```js
const row = overall.find(
  (d) => d.population_id === population && d.measured_month === `${month}-01`
);
```

```js
display(
  row
    ? html`<p>Domains observed <strong>${row.observed_domains}</strong> /
        measured ${row.total_domains} (${row.total_entities} companies)</p>`
    : html`<p>There is no observation for this population and month yet.</p>`
);
```

## By standard

```js
display(
  row
    ? Inputs.table(indicators(row, "en"), { sort: null, rows: 12, width: { Indicator: 300, Domains: 90, Share: 80, Note: 260 } })
    : html`<p>This will appear once an observation exists.</p>`
);
```

**The denominator for every share is the number of domains observed**, not the
number measured. Putting domains that returned SERVFAIL, or nothing at all,
into the denominator would count "could not be observed" as "not configured".

## Counted by company

```js
display(
  row
    ? Inputs.table(entityIndicators(row, "en"), {
        sort: null,
        width: { Indicator: 300, Companies: 90, Share: 80 },
      })
    : html`<p>—</p>`
);
```

## Stated versus effective

```js
display(
  row
    ? Inputs.table(
        [
          { Category: "Stated reject (that is what it says)", Domains: row.nominal_reject_domains },
          { Category: "Effective reject (no pct, t=n, rua present)", Domains: row.enforced_reject_domains },
          { Category: "Enforcing but no rua, so no visibility", Domains: row.blind_reject_domains },
        ],
        { sort: null }
      )
    : html`<p>—</p>`
);
```

## By sector

```js
const sectorRows = sectors
  .filter((d) => d.measured_month === `${month}-01` && d.population_id === population)
  .map((d) => ({
    Sector: d.common12_label,
    Companies: d.n_entities,
    Observed: d.observed_domains,
    SPF: pct(rate(d.spf_adopted_domains, d.observed_domains)),
    DMARC: pct(rate(d.dmarc_adopted_domains, d.observed_domains)),
    Enforced: pct(rate(d.dmarc_enforced_domains, d.observed_domains)),
    Suppressed: d.suppressed ? "folded" : "",
  }));
display(
  sectorRows.length
    ? Inputs.table(sectorRows, { sort: "Sector", rows: 20 })
    : html`<p>There is no sector breakdown for this month yet.</p>`
);
```

## Month by month

```js
const history = overall
  .filter((d) => d.population_id === population)
  .sort((a, b) => b.measured_month.localeCompare(a.measured_month))
  .map((d) => ({
    Month: d.measured_month.slice(0, 7),
    Observed: d.observed_domains,
    SPF: pct(rate(d.spf_adopted_domains, d.observed_domains)),
    DKIM: pct(rate(d.dkim_detected_domains, d.observed_domains)),
    DMARC: pct(rate(d.dmarc_adopted_domains, d.observed_domains)),
    Enforced: pct(rate(d.dmarc_enforced_domains, d.observed_domains)),
    "Effective reject": pct(rate(d.enforced_reject_domains, d.observed_domains)),
    DNSSEC: pct(rate(d.dnssec_domains, d.observed_domains)),
  }));
display(
  history.length
    ? Inputs.table(history, { sort: null, rows: 24 })
    : html`<p>This will appear once an observation exists.</p>`
);
```

**Comparing across months needs care.** If the population or a dictionary
changes, the figures move even when nothing changed in the world. What changed
is recorded in the [change log](/changelog).

## Licence

```js
display(html`<p>The aggregated results are published under <strong>${meta.license ?? "CC0-1.0"}</strong>.
They can be used freely without attribution, but for the primary sources below,
follow each provider's own terms.</p>`);
```

```js
display(html`<ul>${(meta.attribution ?? []).map((t) => html`<li>${t}</li>`)}</ul>`);
```

## Downloads

The same figures as the tables above, for working with locally.

```js
display(
  html`<ul>
    <li><a href="../data/stats_overall.json">stats_overall.json</a> ── overall statistics, all months</li>
    <li><a href="../data/stats_overall.csv">stats_overall.csv</a></li>
    <li><a href="../data/stats_by_sector.json">stats_by_sector.json</a> ── sector breakdown, all months</li>
    <li><a href="../data/stats_by_sector.csv">stats_by_sector.csv</a></li>
    <li><a href="../data/meta.json">meta.json</a> ── months, licence, limits of the measurement</li>
  </ul>`
);
```

Parquet files are kept per month. This site references them rather than
copying, so that the same figures cannot go stale in one place and not the
other.

## What the columns mean

The main columns used in the tables above.

| Column | Meaning |
|---|---|
| `total_domains` | domains taken into measurement |
| `observed_domains` | **of those, how many could be observed. This is the denominator for every share** |
| `spf_adopted_domains` | domains publishing SPF |
| `spf_adopted_entities` | companies publishing SPF |
| `dmarc_enforced_domains` | `quarantine` or `reject` |
| `nominal_reject_domains` | the record says `p=reject` |
| `enforced_reject_domains` | no `pct`, `t=n`, `rua` present |
| `blind_reject_domains` | enforcing, but no `rua` |
| `dkim_detected_domains` | found among known selectors |
| `dkim_not_found_domains` | not found among known selectors. **This is not the same as unset** |
| `mta_sts_domains` | publishes MTA-STS |
| `tls_rpt_domains` | publishes TLS-RPT |
| `bimi_domains` | publishes BIMI |
| `dnssec_domains` | signed with DNSSEC |
| `dane_domains` | has a TLSA record |
| `dane_orphan` | has TLSA, but DNSSEC could not be confirmed |
| `maturity_stage_dist` | distribution across maturity stages 0–4 (JSON) |
| `parked_hardened` | Null MX + `-all` + `p=reject` |
| `parked_neglected` | none of MX, SPF or DMARC |
| `delta_prev_month` | difference from the previous month (JSON) |

Both company-based and domain-based counts are published. Counted by domain
alone, companies holding many domains carry more weight.

## About per-company detail

Detail naming individual companies is kept behind authentication only.
**It is not being published at present.**
