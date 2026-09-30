---
title: Methodology
footer: This site measures conformance to published standards and is not an overall security assessment. Data is <a href="/en/terms">CC0</a>.
---

# Methodology

## What is measured

Only records published in DNS: SPF, DKIM and DMARC, plus MTA-STS, TLS-RPT,
BIMI, DNSSEC and DANE. Neither message content nor mail delivery is examined.
**This site measures conformance to published standards and is not an overall
security assessment.**

## The eight phases

| Phase | What it does |
|---|---|
| P1 Population | Fixes the set of listed companies from a primary register (**which register depends on the population** — see below) |
| P2 Domain candidates | Widens the candidate set from official sites, CT logs, SPF `redirect=` / `include:`, DMARC `rua=` and a manual dictionary |
| P3 Mail domains | Narrows the candidates and assigns a confidence (confirmed / likely / unknown / parked) |
| P4 DNS measurement | Fetches the records and stores the responses verbatim |
| P5 Parsing | Interprets the stored responses against the specifications |
| P6 Inference | Matches against dictionaries to infer mail platforms and products |
| P7 Aggregation | Builds the published statistics and the month-on-month differences |
| P8 Publication | Generates this site |

Each phase runs independently, and always records how many items it processed
and how many failed.

### The primary register differs by population

| Population | Primary register | Filled in from |
|---|---|---|
| Listed companies in Japan | FSA EDINET code list | NTA corporate number (name, address), gBizINFO (official site) |
| Listed companies in the US | SEC EDGAR (public domain) | Wikidata (official site; SEC's website field is almost always empty) |

**EDINET is a Japanese register, so it is not used for the US population.**
The same applies to sectors: Japan's come from EDINET's 33 sectors and the
US's from SEC SIC codes, each mapped through ISIC Rev.4 into the twelve
common classes.

Attribution appears at the foot of the page, and **changes with the
populations being published.**

## "Could not be observed" is kept apart from "was not there"

A SERVFAIL or a timeout is not proof that a record is absent. The two are
treated as different facts.

- Rates are calculated **only over domains that could be observed**
- A domain seen last month but not observed this month is not counted as
  having disappeared. Disappearance is recorded only when the domain was
  observed twice in a row and found absent

## Companies with no starting domain

Measurement starts from each company's official website domain and expands
outwards to candidate domains. **A company whose official website is unknown
produces no candidate domains at all.**

Such companies are not in the denominator of any rate. The "Companies" figure
in the summary is the size of the population, **not a claim that all of them
were measured.** Where the two differ, the count is shown next to the figure.

Official websites are looked up by corporate number against the Japanese
government company registry (gBizINFO) and Wikidata. Some companies appear in
neither. **No guesses are made** — measuring another company's domain by
mistake is a worse error than not measuring at all.

## Some domains are excluded from measurement

Once a domain is excluded, no further DNS queries are sent to it. **"Excluded"
is neither "could not be observed" nor "no record existed". It is a third
state: a decision not to measure.**

The number excluded is recorded in each month's run record, because **dropping
domains from the denominator silently would move the rates in a way nobody
could account for, and a reader could not tell it apart from a measurement
failure.**

## Facts and inferences are kept apart

Values read directly from DNS and inferences drawn from dictionary matching
are treated as different things, and are separated visually on this site.
Every inference carries a confidence level and the evidence behind it.

## How the mail platform is inferred

An inference such as "this domain appears to run on Microsoft 365" comes from
**matching four kinds of DNS trace against a dictionary of patterns**. For
Microsoft 365 they are:

| Trace | What it is | Example |
|---|---|---|
| MX | the host that accepts incoming mail | `<name>.mail.protection.outlook.com` |
| SPF `include:` | who is permitted to send | `spf.protection.outlook.com` |
| DKIM CNAME | where the signing key lives | `<selector>._domainkey` → `*.onmicrosoft.com` |
| Verification TXT | the value placed at sign-up | `MS=ms########` |

**None of these is a record of a contract.** They say what is published in
DNS, and nothing more.

### The traces differ in strength

They are treated in this order, strongest first:
**DKIM CNAME ≥ MX > SPF `include:` > verification TXT.**

- **The DKIM CNAME is the strongest.** A signing key's location means nothing
  unless signing actually happens there
- **MX points at reception, but a front-end product may hold it.** Where it
  does, MX shows only the front-end; the platform behind it is not visible
- **An `include:` only permits sending**, which is not the same as using it
- **The verification TXT is the weakest.** It is commonly left behind after a
  service is dropped. Where it is the only evidence, the confidence is set to
  `low` and whether any MX / SPF / DKIM corroborates it is recorded separately

Two or more kinds of trace raise the confidence one step. A DKIM CNAME sets it
to `high`.

### Front-ends and platforms are published separately

Where a product holds the MX record, reading MX alone suggests that product is
the company's platform. **That erases the platform sitting behind it.**

So the inferences are split into three layers and counted separately.

| Layer | What it is |
|---|---|
| Platform | where the mailboxes are |
| Inbound front-end | products holding the MX record; a platform sits behind them |
| Outbound front-end | products that do not hold MX and only handle outgoing mail |

**One company can appear in all three layers.** We have observed companies
using different vendors for inbound and outbound mail. Within one layer a
domain is counted once — where several traces appear, the stronger evidence
wins, because counting both would push the total past 100%.

Where one engine is sold under several names (OEM), **each seller gets its own
row and the engine is named alongside it**. Folding them together loses whose
customers they are; leaving them apart loses that the mechanism is the same.

### Reasons for "unknown" are kept apart

"The platform is unknown" covers several different situations, and mixing them
invites misreading.

| Reason | What it means |
|---|---|
| API-integrated | the product never enters the delivery path. **It cannot appear in DNS at all** |
| MX inside their own domain | mail is being handled; the product simply is not visible |
| SPF flattened | `include:` has been expanded to IP ranges, leaving no vendor name |
| Tenant placeholder MX | the domain is registered, but reception does not happen there |
| Not observed | the query did not return. That is not "absent" |

**None of these means "not in use."** They enter neither the numerator nor the
denominator of any adoption share.

### About the dictionary

The matching dictionary is a configuration file, and **its version is recorded
alongside the results**, so it can later be traced which dictionary produced a
given figure.

MX hosts not in the dictionary are published each month as a worklist, most
frequent first. **A dictionary always lags** — how far it lags is made visible.

Vendors with fewer than five companies are folded into "Other". The counts are
kept.

## Which domains are measured

Measurement starts from each company's **registered domain** (the eTLD+1, such
as `example.com`) and widens from there.

| Route | What it is |
|---|---|
| Official site | the official URL from government registries and Wikidata |
| CT logs | names appearing in Certificate Transparency logs |
| SPF `redirect=` | where SPF is delegated |
| SPF `include:` | own subdomains the record has been split across |
| DMARC `rua=` | the report destination, where it is the company's own domain |
| Certificate organization | **only for companies with no starting domain**: a search of certificate logs by organization name |
| Manual dictionary | group companies and business brands, added by hand |

### Companies with no starting domain

Measurement starts from a company's official website domain, but **for some
companies that domain is not known**. No candidates are built for them by any
of the routes above.

**That does not mean they use no email.** It means the domain has not been
found.

So, for those companies only, the certificate logs are searched by
**organization name**. Certificates that verify the existence of the
organization (OV and EV) carry an organization field that the issuing
authority fills in after checking the company register. **Reading that is not
guesswork.** For Japanese companies the Latin-script name comes from the
EDINET register.

**That search also returns companies with similar names.** In one measurement,
6,721 of 10,000 results for `Toyota Motor Corporation` belonged to
`Toyota Motor Credit Corporation`, a different company. Only certificates whose
organization name matches **exactly** are used, and suffixes such as `Inc.` or
`Corporation` are not stripped — stripping them only makes different companies
harder to tell apart.

<div class="limits">

- **Free certificates carry no organization field.** A company using only
  those cannot be found by this route. **Not being found does not mean not
  using email**
- Certificate log searches return a capped number of results. Where the cap is
  reached and no exact match survives, that is recorded as **"could not be
  established", not as "none"**

</div>

### A company's mail can sit on a subdomain

Some companies do not run mail on their registered domain. The report
destination in `_dmarc.example.com` points at `mail.example.com`, and **that
name carries an MX record and a DMARC record of its own**. In that case,
figures drawn from the registered domain alone say nothing about the company's
actual mail.

So **subdomains found through traces of mail** — `rua=` destinations, SPF
`redirect=` / `include:` targets, the manual dictionary — are kept rather than
rolled up, and those **carrying an MX record or a `_dmarc` record of their
own** are measured. Names appearing only in certificate logs (used for
delivery or validation) carry neither, so they fall away.

- **Subdomains enter no other share's denominator.** Mixing them in would raise
  the domain count and break comparison with earlier months, so they are
  counted apart
- **Only what was found.** There is no way to enumerate every subdomain from
  DNS. Absence here does not mean absence
- The routes are limited to those three. A subdomain sending mail by any other
  route is not visible here

### `p=` and `sp=` are different things

A DMARC `p=` tag applies to the domain itself. **What applies to its subdomains
is `sp=`, and when that is absent they inherit `p=`.**

A record reading `p=reject; sp=none` applies `reject` to the domain itself and
`none` to everything under it. Both are counted separately.

**A subdomain carrying its own `_dmarc` record does not inherit `sp=`**, and
where that is the case the `sp=` figures do not describe it.

For subdomains that were measured, one **without** its own `_dmarc` is counted
as inheriting `sp=` from above. Inherited and own policies are reported
separately.

<div class="limits">

- **Only the immediate parent is consulted.** A different setting on
  `b.example.com` under `a.b.example.com` is invisible unless that name was
  itself measured
- Where the parent was not measured, the subdomain is treated as **not
  inheriting**. That is "the parent was not looked at", not "there is no parent"

</div>

## What cannot be detected

### API-integrated email security products

Products that integrate over an API or OAuth without changing the MX record
leave no trace in DNS, by construction. **Not detecting one does not mean it
is not in use.**

### DKIM selectors

DKIM selectors cannot be enumerated from DNS. They are queried against a
dictionary of known selectors, so a selector absent from that dictionary
cannot be found. "Not found among known selectors" and "not configured" are
recorded as different states.

### MTA-STS mode

Whether the policy is `enforce` or `testing` cannot be determined from DNS.
It requires fetching the policy file over HTTPS, which is not currently done.

### Forwarding paths

The effect of DMARC depends on forwarding and mailing-list paths, which DNS
observation cannot reveal.

## DMARC enforcement is computed two ways

RFC 7489 honours `pct`, while RFC 9989 (DMARCbis) drops `pct` and treats `t=y`
as test mode. The two readings do not always agree, so both are computed and
stored.

## Organizational domain is also resolved two ways

Resolution via the Public Suffix List and resolution via the RFC 9989 Tree Walk
are both computed. They can differ under `.co.jp`-style suffixes and on
deeply nested subdomains.

## Sources

```js
const meta = FileAttachment("../data/meta.json").json();
display(html`<ul>${(meta.attribution ?? []).map((t) => html`<li>${t}</li>`)}</ul>`);
```

## The measurement can be reproduced

DNS responses are stored unmodified, so that if a bug is found in the
interpretation, the results can be rebuilt from the stored responses. The
versions of the dictionaries used in the aggregation are recorded in the
output, and the measurement code and configuration are kept at the same
version as the results.
