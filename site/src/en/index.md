---
title: Email DNS Monitor
footer: This site measures conformance to published standards and is not an overall security assessment. Data is <a href="/en/terms">CC0</a>.
---

# Email DNS Monitor

<div class="subtitle">Monthly observation of email authentication</div>

Each month, the publicly available DNS records of listed companies in Japan and the United States
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
import { table } from "../components/table.js";
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
// Companies that produced no candidate domain at all, because no official
// website could be found. **They are not in the denominator of any rate.**
// Hiding this makes the reader take total_entities as the number measured.
const entityGap = Math.max(
  0,
  (current?.total_entities ?? 0) - (current?.entities_with_domains ?? 0)
);
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
      <span class="readout-cap">${entityGap > 0
        ? html`${population}<br><strong>${num(entityGap)} with no starting domain, not in any rate</strong>`
        : population}</span>
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

```js
// Nothing is shown when there is no gap. **A note that says "0 companies
// missing" every month turns into background and stops being read.**
display(
  entityGap > 0
    ? html`<p class="gap-note">No official website could be found for
        <strong>${num(entityGap)}</strong> companies, so they have no candidate
        domains and are not included in the rates above. The rates describe
        ${num(current?.entities_with_domains)} companies.
        <a href="./methodology#companies-with-no-starting-domain">How this is measured</a></p>`
    : html``
);
```

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
display(table(indicators(current ?? {}, "en"), { sort: null, rows: 12, width: { Indicator: 300, Domains: 90, Share: 80, Note: 260 } }));
```

Bar length is good for seeing a trend, but it does not answer "how many
domains publish SPF". Counts and shares are given together. **The denominator
for every share is the number of domains observed.**

### Counted by company

```js
display(
  table(entityIndicators(current ?? {}, "en"), {
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
  table(
    [
      { Category: "Stated reject (that is what it says)", Domains: current?.nominal_reject_domains },
      { Category: "Effective reject (no pct, t=n, rua present)", Domains: current?.enforced_reject_domains },
      { Category: "Enforcing but no rua, so no visibility", Domains: current?.blind_reject_domains },
    ],
    { sort: null }
  )
);
```

### Whether aggregate reports have a destination

DMARC's `rua=` is where receivers send aggregate reports. **Without it, the
domain owner cannot see how receivers handled mail sent under the domain's
name.** `p=none` is the setting for "observe first", but without a destination
nothing arrives to observe.

```js
display(
  current?.dmarc_no_rua_domains == null
    ? html`<p class="muted">Not counted for this month yet. <strong>This is not zero.</strong></p>`
    : table(
        [
          { Category: "publishes DMARC", Domains: current?.dmarc_adopted_domains },
          { Category: "of which rua (aggregate report destination) is absent", Domains: current?.dmarc_no_rua_domains },
          { Category: "of which p=none with no rua", Domains: current?.dmarc_none_no_rua_domains },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- Only the `_dmarc` record of the main domain is read. Some companies see
  reports through other means, such as a receiver's dashboard
- Whether `ruf=` (individual failure reports) is present is not checked

</div>

### Whether report destinations accept the reports

When `rua` names a destination outside the domain itself (a group company or a
report-processing service), **the destination domain has to publish a record
saying it accepts them** (`<domain>._report._dmarc.<destination>`). Without it,
receivers may not send the reports — a domain can publish DMARC and still not
be receiving its aggregate reports.

```js
display(
  current?.rua_external_domains === undefined
    ? html`<p class="muted">Not counted for this month yet. <strong>This is not zero.</strong></p>`
    : table(
        [
          { Category: "rua has an outside destination", Domains: current?.rua_external_domains },
          { Category: "of which the destination does not publish the accepting record", Domains: current?.rua_unauthorized_domains },
          { Category: "of which the destination domain is not registered", Domains: current?.rua_unregistered_domains },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- Destinations that could not be looked up are not counted as missing the record
- Some receivers send without checking this record, so it does not show the
  reports are not arriving
- If the destination domain is unregistered, whoever registers it could receive
  the reports

</div>

### Whether SPF stays within its DNS lookup limit

The SPF terms that make DNS lookups (`include`, `a`, `mx`, `exists`, `redirect`
and so on) are limited to **10 in total, including those inside the `include`d
records** (RFC 7208 §4.6.4). Past that, the receiver's evaluation can stop with
an error (PermError) and SPF may not take effect. Counting only the domain's own
record makes a domain with three `include`s look like "3" even when the records
behind them push it past 10, so **the included records are looked up and counted
as well.**

```js
display(
  current?.spf_lookup_exceeded_domains == null
    ? html`<p class="muted">Not counted for this month yet. <strong>This is not zero.</strong></p>`
    : table(
        [
          { Category: "publishes SPF", Domains: current?.spf_adopted_domains },
          { Category: "of which the count including included records exceeds 10", Domains: current?.spf_lookup_exceeded_domains },
          { Category: "of which an include or redirect target has no SPF (or two)", Domains: current?.spf_target_broken_domains },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- The count follows every `include` and `redirect`. Receivers stop evaluating at
  the first match, so **a domain over the limit may still pass for some senders**
- Domains that could not be counted to the end (targets with macros such as
  `%{i}`, or targets that could not be looked up) are in neither row, unless the
  limit was already exceeded
- Separate limits, such as the number of names behind an `mx` term, are not checked

</div>

The final `all` term of an SPF record tells receivers **what to do with senders
not on the list**.

```js
display(
  current?.spf_all_fail_domains == null
    ? html`<p class="muted">Not counted for this month yet. <strong>This is not zero.</strong></p>`
    : table(
        [
          { "Final term": "-all", Meaning: "senders not listed fail", Domains: current?.spf_all_fail_domains },
          { "Final term": "~all", Meaning: "senders not listed are suspect (softfail)", Domains: current?.spf_all_softfail_domains },
          { "Final term": "?all", Meaning: "says nothing about senders not listed", Domains: current?.spf_all_neutral_domains },
          { "Final term": "+all (or all)", Meaning: "every sender passes", Domains: current?.spf_all_pass_domains },
          { "Final term": "no all", Meaning: "says nothing about senders not listed", Domains: current?.spf_all_missing_domains },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- Choosing between `-all` and `~all` depends on how a domain operates SPF
  together with DMARC. The table lists counts and does not rank them
- An SPF record ending in `+all` lets anyone pass SPF
- Domains with two `v=spf1` records are left out, since the whole record is invalid
- When the record hands over to another with `redirect=`, **the `all` of that
  other record** is counted. Domains whose target could not be looked up are left
  out of the table

</div>

### Settings that are easy to miss

None of the settings below necessarily shows up right away as lost mail or
spoofing, so they tend to go unnoticed.

```js
display(
  current?.txt_apex_oversize_domains == null
    ? html`<p class="muted">Not yet counted for this month. <strong>This is not zero.</strong></p>`
    : table(
        [
          { Category: "an SPF include or redirect target's domain is not registered", Domains: current?.spf_dangling_domains },
          { Category: "the apex TXT response exceeds 1,232 bytes (estimate)", Domains: current?.txt_apex_oversize_domains },
          { Category: "SPF lets through more than 65,536 IPv4 addresses", Domains: current?.spf_ip4_broad_domains },
          { Category: "the DKIM keys found are Ed25519 only", Domains: current?.dkim_ed25519_only_domains },
          { Category: "DMARC carries tags removed in RFC 9989 (pct, rf, ri)", Domains: current?.dmarc_historic_tags_domains },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- **Unregistered targets**: if someone registers a domain that SPF names, they
  can send mail that passes SPF as this domain (the SubdoMailing campaign
  reported in 2024 worked this way). Only cases where the registrable domain's
  NS returned NXDOMAIN and the suffix above it exists are counted
- **TXT size**: as verification tokens pile up, the apex TXT response can exceed
  the size usually returned over UDP (1,232 bytes, from DNS Flag Day 2020) and
  needs a retry over TCP. Some receivers then return a temporary SPF error.
  The figure is estimated from the record contents, not the actual response size
- **Wide IP ranges**: when SPF lists a wide range in `ip4:` (such as a cloud or
  hosting range), others who can use that range also pass SPF as this domain.
  Only `ip4:` terms in the domain's own record are counted (not `include`
  targets); 65,536 is one IPv4 /16. Some companies hold large ranges of their
  own, so a large figure is not an error in itself
- **Ed25519-only DKIM**: some receivers do not verify Ed25519 (RFC 8463)
  signatures. Publishing an RSA key alongside is the recommended form. Only the
  selectors this project tried are covered
- **Removed tags**: `pct=`, `rf=` and `ri=` were removed in RFC 9989. Receivers
  that follow RFC 7489 still apply them, so they are not errors

</div>

### Tags from the new DMARC specification (RFC 9989)

RFC 9989 added a policy for non-existent subdomains (`np=`) and `t=y` to mark
a policy under test, and removed `pct=`, `rf=` and `ri=`.

```js
display(
  current?.dmarc_np_domains === undefined
    ? html`<p class="muted">Not yet counted for this month. <strong>This is not zero.</strong></p>`
    : table(
        [
          { Tag: "np= (policy for non-existent subdomains)", Domains: current?.dmarc_np_domains },
          { Tag: "t=y (testing; receivers apply one step weaker)", Domains: current?.dmarc_testing_domains },
          { Tag: "any of pct=, rf=, ri= (removed tags)", Domains: current?.dmarc_historic_tags_domains },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- Without `np=`, `sp=` applies, and without that, `p=`; a missing `np=` is not
  an error in itself
- Which specification a receiver follows cannot be seen from outside

</div>

## The domain, and what sits under it

A DMARC `p=` tag applies to the domain itself. **What applies to its
subdomains is `sp=`, and when that is absent they inherit `p=`.**

A record reading `p=reject; sp=none` applies `reject` to the domain itself and
**`none` to everything under it.** A company's real mail commonly sits on a
subdomain.

```js
// **Do not show a missing column as zero** (principle 5). Gold produced
// before this section exists has no such column; an empty cell reads as none
display(
  current?.sp_enforced_domains === undefined
    ? html`<p class="muted">This month has no <code>sp=</code> figures yet. <strong>That is not zero.</strong></p>`
    : table(
        [
          { Category: "Applies to the domain itself (p=)", Domains: current?.dmarc_enforced_domains },
          { Category: "Applies to subdomains too (sp= or inherited)", Domains: current?.sp_enforced_domains },
          { Category: "Weakened for subdomains via sp=", Domains: current?.sp_weaker_domains },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- **What appears here is only what `sp=` says.** A subdomain carrying its own
  `_dmarc` record does not inherit `sp=`, so these figures do not describe it
- Subdomains that were actually queried are counted separately, below

</div>

### Subdomains actually queried

Subdomains found through traces of mail — a `rua` destination, an SPF
`include:` — that **carried an MX record or a `_dmarc` record of their own**.

```js
display(
  current?.subdomains_measured === undefined
    ? html`<p class="muted">No subdomains were measured in this month. <strong>That is not the same as none existing.</strong></p>`
    : table(
        [
          { Category: "Subdomains measured", Count: current?.subdomains_measured },
          { Category: "Of those, observed", Count: current?.subdomains_observed },
          { Category: "Carrying their own DMARC", Count: current?.subdomains_with_own_dmarc },
      { Category: "Inheriting sp= from above", Count: current?.subdomains_inherited_policy },
          { Category: "Of those, quarantine or stronger", Count: current?.subdomains_dmarc_enforced },
          { Category: "Companies with mail on a subdomain", Count: current?.entities_with_subdomain_mail },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- **These counts enter no denominator anywhere else on this page.** Mixing them
  in would raise the domain count and break comparison with earlier months, so
  they are kept apart
- **Only what was found.** There is no way to enumerate every subdomain.
  Absence here does not mean absence
- The routes used are `rua` destinations and SPF `include:` targets, plus,
  from the 2026-12 measurement, `mail.` and `email.` under the primary domain.
  A subdomain sending mail by any other route is not visible here

</div>

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

### Changes from the previous month

Counted **only for the same domains observed in both months**: how the
effective DMARC policy changed from the previous month. This removes the effect
of listings, delistings and changes in the candidate domains.

```js
const delta = JSON.parse(current?.delta_prev_month ?? "null");
const STATES = ["no_dmarc", "none", "quarantine", "reject"];
const STATE_EN = { no_dmarc: "no DMARC", none: "none", quarantine: "quarantine", reject: "reject" };
display(
  !delta || delta.skipped || !delta.policy_transitions
    ? html`<p class="muted">No earlier measurement to compare with yet.</p>`
    : html`${table(
        STATES.map((from) => Object.fromEntries([
          ["previous \\ this month", STATE_EN[from]],
          ...STATES.map((to) => [STATE_EN[to], delta.policy_transitions[`${from}>${to}`] ?? 0]),
        ])),
        { sort: null }
      )}<p>Among domains with DMARC in both months, rua was added on ${num(delta.rua_added)} and removed on ${num(delta.rua_removed)}.</p>`
);
```

<div class="limits">

- Policies are effective values (`quarantine` with `pct=` under 100 is counted as `quarantine`)
- Domains not observed this month are counted neither as changed nor as unchanged

</div>

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

### The DNS-visible part of bulk-sender requirements

Gmail and Yahoo (February 2024) and Microsoft Outlook.com (May 2025) ask
senders of 5,000 or more messages a day to have SPF, DKIM and DMARC (`p=none`
is enough). Among sending domains (with MX), these are the counts that meet
the part visible in DNS.

```js
display(
  current?.bulk_ready_domains === undefined
    ? html`<p class="muted">Not yet counted for this month. <strong>This is not zero.</strong></p>`
    : table(
        [
          { Item: "Sending domains", Domains: current?.sending_domains, Share: "" },
          { Item: "SPF present and evaluates without error", Domains: current?.bulk_spf_ok_domains, Share: pct(rate(current?.bulk_spf_ok_domains, current?.sending_domains)) },
          { Item: "DKIM key found", Domains: current?.bulk_dkim_domains, Share: pct(rate(current?.bulk_dkim_domains, current?.sending_domains)) },
          { Item: "DMARC present with a valid record", Domains: current?.bulk_dmarc_ok_domains, Share: pct(rate(current?.bulk_dmarc_ok_domains, current?.sending_domains)) },
          { Item: "All three", Domains: current?.bulk_ready_domains, Share: pct(rate(current?.bulk_ready_domains, current?.sending_domains)) },
        ],
        { sort: null }
      )
);
```

<div class="limits">

- The requirements apply to senders of large volumes; **not every domain sends
  that much.** Missing items do not by themselves mean mail is refused
- SPF/DKIM alignment with DMARC, reverse DNS of sending IPs, TLS, one-click
  unsubscribe and complaint rates are not visible in DNS and are not counted
- DKIM counts only keys found under the selectors this project tried. Domains
  where none was found may still have keys, so this row reads low
- "Evaluates without error" means a single `v=spf1` record that stays within
  the limit of 10 lookups counted through `include` targets

</div>

## Domains that do not send

Most of the domains expanded in the candidate set are not used for sending.
A domain with no sending history and nobody watching it can become a starting
point for spoofing. So domains explicitly locked down are shown separately
from domains with nothing configured.

```js
display(
  table(
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
