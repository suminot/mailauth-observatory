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
import { rate, pct, indicators, entityIndicators, populationLabel } from "../components/format.js";
import { table } from "../components/table.js";
```

The observed figures can be read on this page. **You should not have to
download anything to see what is in the data.**

```js
const populations = [...new Set(overall.map((d) => d.population_id))];
```

```js
const population = view(
  Inputs.select(populations, { label: "Population", value: populations[0], format: (d) => populationLabel(d, "en") })
);
```

```js
// **Offer only the months this population was measured in.** Defaulting to
// the latest month overall opens on "No data" for a population that was not
// measured that month (2026-10 covers the US only)
const months = [...new Set(overall.filter((d) => d.population_id === population).map((d) => d.measured_month.slice(0, 7)))].sort().reverse();
const month = view(Inputs.select(months, { label: "Month", value: months[0] }));
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
    ? table(indicators(row, "en"), { sort: null, rows: 12, width: { Indicator: 300, Domains: 90, Share: 80, Note: 260 } })
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
    ? table(entityIndicators(row, "en"), {
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
    ? table(
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
    ? table(sectorRows, { sort: "Sector", rows: 20 })
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
    ? table(history, { sort: null, rows: 24 })
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
| `entities_with_domains` | companies with at least one candidate domain. **The difference from `total_entities` is the companies that never entered the measurement** |
| `excluded_non_operating` | **entities that are not operating companies (SPACs, funds, ETFs), left out of the population.** Not counted in `total_entities`. Names and reasons are on the methodology page |
| `spf_adopted_domains` | domains publishing SPF |
| `spf_adopted_entities` | companies publishing SPF |
| `dmarc_enforced_domains` | `quarantine` or `reject` |
| `nominal_reject_domains` | the record says `p=reject` |
| `enforced_reject_domains` | no `pct`, `t=n`, `rua` present |
| `blind_reject_domains` | enforcing, but no `rua` |
| `sp_enforced_domains` | **the strength that applies to subdomains** is `quarantine` or stronger. Absent `sp=`, they inherit `p=` |
| `sp_weaker_domains` | `sp=` is weaker than `p=`. **A different strength applies to the domain itself and to what sits under it** |
| `subdomains_measured` | **subdomains actually queried.** These enter no share's denominator above; they are counted apart |
| `subdomains_with_own_dmarc` | the subdomain carries its own `_dmarc` and does not inherit `sp=` |
| `subdomains_inherited_policy` | has no `_dmarc` of its own and inherits `sp=` from above |
| `entities_with_subdomain_mail` | companies found handling mail on a subdomain |
| `dkim_detected_domains` | found among known selectors |
| `dkim_not_found_domains` | not found among known selectors. **This is not the same as unset** |
| `dkim_other_selector_domains` | of those, `_domainkey.<domain>` is not NXDOMAIN, so names exist under it (likely keys under selectors not tried). Some DNS servers answer this wrongly, so treat it as a guide. Empty in months without the column |
| `mta_sts_domains` | publishes MTA-STS (has the `_mta-sts` TXT) |
| `mta_sts_enforce_domains` | the policy fetched over HTTPS says `mode: enforce`. Empty for months where it was not fetched |
| `mta_sts_testing_domains` | the policy says `mode: testing` (failures are reported, delivery is not stopped) |
| `mta_sts_unusable_domains` | has the TXT, but the policy could not be fetched or read. **Senders cannot read it either, so it has no effect** |
| `mta_sts_mx_mismatch_domains` | the actual MX is not listed in the policy's `mx` |
| `tls_rpt_domains` | publishes TLS-RPT |
| `rua_external_domains` | the DMARC aggregate report address (`rua=`) includes a destination outside the domain itself |
| `rua_unauthorized_domains` | of those, the destination does not publish the record that accepts the reports (`<domain>._report._dmarc.<destination>`). **Receivers may not send the reports.** Lookups that failed are not counted |
| `rua_unregistered_domains` | of those, the destination domain was confirmed to be unregistered (whoever registers it could receive the reports) |
| `spf_lookup_exceeded_domains` | the SPF terms that make DNS lookups exceed 10 when the `include` and `redirect` targets are counted too. Domains that could not be counted to the end are left out unless already over the limit. Empty in months where the targets were not looked up |
| `spf_target_broken_domains` | an SPF `include` or `redirect` target has no SPF record, or two (the receiver's evaluation errors). Empty in months where the targets were not looked up |
| `spf_all_fail_domains` | the final SPF term is `-all`. These five columns leave out domains with two `v=spf1` records. Records that hand over with `redirect=` are counted by the `all` of the target |
| `spf_all_softfail_domains` | the final SPF term is `~all` |
| `spf_all_neutral_domains` | the final SPF term is `?all` |
| `spf_all_pass_domains` | the final SPF term is `+all` (including a bare `all`) |
| `spf_all_missing_domains` | the SPF record has no `all` |
| `spf_all_unknown_domains` | the `redirect=` target could not be looked up, so the final `all` is unknown (in none of the five columns above) |
| `spf_dangling_domains` | among SPF `include` or `redirect` targets with no SPF record, one whose registrable domain was confirmed unregistered (anyone registering it could send mail that passes SPF). Domains with targets that could not be looked up are not counted. Empty in months without the column |
| `txt_apex_oversize_domains` | the estimated size of the apex TXT response exceeds 1,232 bytes. Not the actual response size. Empty in months without the column |
| `spf_ip4_broad_domains` | the IPv4 addresses the apex SPF record lets through (`ip4:`, overlaps counted once, `include` targets not counted) exceed 65,536 (one /16). Empty in months without the column |
| `rua_placeholder_domains` | the DMARC rua destination is a name reserved for examples (`example.com` and the like, RFC 2606 and 6761) |
| `reject_with_rua_domains` | `p=reject` with a rua destination (names reserved for examples excluded) |
| `dmarc_np_domains` | DMARC carries a valid `np=` (policy for non-existent subdomains, RFC 9989) |
| `dmarc_testing_domains` | DMARC carries `t=y` (testing, RFC 9989) |
| `evidence_signed_entities` | per company, a reachable MX, SPF senders and a DKIM key all present on one of its main domains (the four columns are mutually exclusive) |
| `evidence_sending_entities` | MX and SPF senders, but no DKIM key found under the selectors tried |
| `evidence_dkim_hint_entities` | of `evidence_sending_entities`, those with names under `_domainkey.<domain>` (likely signing under selectors not tried). A subset. Empty in months without the column |
| `evidence_receiving_entities` | MX, but no senders listed in SPF |
| `evidence_none_entities` | no reachable MX on any main domain |
| `mail_off_site_entities` | no reachable MX on the website's domain, but another main domain of the same company has one |
| `ownership_evidence_dist` | per main domain, the grounds for tying it to the company (`company_stated`, `certificate`, `own_dns`, `web_link`, `other`) × the stage of mail-use evidence (`signed`, `sending`, `receiving`, `none`), as domain counts (JSON) |
| `subdomains_found` | subdomains found (certificate logs, common names and the like; whether measured or not; web host names included) |
| `subdomains_found_dmarc` | of those, subdomains whose registrable domain has DMARC (covered by inheritance). For comparison with published studies that count subdomains |
| `bulk_spf_ok_domains` | among sending domains (`sending_domains`), SPF is present and evaluates without error (one `v=spf1`, within 10 lookups counted through `include` targets) |
| `bulk_dkim_domains` | likewise, a DKIM key was found (within the selectors tried) |
| `bulk_dmarc_ok_domains` | likewise, DMARC is present with a valid record (including `p=none`) |
| `bulk_ready_domains` | likewise, all three of the above (the DNS-visible part of bulk-sender requirements; alignment and the rest are not checked) |
| `dkim_ed25519_only_domains` | the DKIM keys found (excluding revoked keys and the wildcard) are Ed25519 only. Empty in months without the column |
| `dmarc_historic_tags_domains` | DMARC carries tags removed in RFC 9989 (`pct=`, `rf=`, `ri=`). Not an error. Empty in months without the column |
| `dmarc_invalid_domains` | publishes DMARC, but the record is invalid (two `v=DMARC1` records, or `p=` missing or invalid). Included in `dmarc_adopted_domains` |
| `dkim_key_2048_domains` | of the domains where DKIM was found, every RSA key found is 2048 bits or longer |
| `dkim_key_1024_domains` | likewise, the shortest RSA key found is 1024 bits |
| `dkim_key_short_domains` | likewise, the shortest RSA key found is under 1024 bits (RFC 8301 says receivers must not treat signatures with such keys as valid) |
| `dmarc_no_rua_domains` | publishes DMARC, but has no aggregate report destination (`rua=`) |
| `dmarc_none_no_rua_domains` | of those, `p=none` |
| `bimi_domains` | publishes BIMI |
| `bimi_vmc_domains` | of those, gives a certificate location (`a=`, such as a VMC). Whether the certificate is valid is not checked |
| `dnssec_domains` | signed with DNSSEC |
| `dane_domains` | has a TLSA record |
| `dane_orphan` | has TLSA, but DNSSEC could not be confirmed |
| `maturity_stage_dist` | distribution across maturity stages 0–4 (JSON) |
| `parked_hardened` | Null MX + `-all` + `p=reject` |
| `parked_neglected` | none of MX, SPF or DMARC |
| `delta_prev_month` | difference from the previous month (JSON). `policy_transitions` counts changes in the effective DMARC policy for the same domains observed in both months (keys like `"none>reject"`); `rua_added` and `rua_removed` count rua being added or removed |

Both company-based and domain-based counts are published. Counted by domain
alone, companies holding many domains carry more weight.

## About per-company detail

Detail naming individual companies is kept behind authentication only.
**It is not being published at present.**
