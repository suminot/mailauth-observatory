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
| Listed companies in Japan | FSA EDINET code list | NTA corporate number (name, address), gBizINFO, Wikidata, annual securities reports and Japanese Wikipedia (official site) |
| Listed companies in the US | SEC EDGAR (public domain) | Wikidata (official site; SEC's website field is almost always empty), annual reports, prospectuses and proxy statements filed with the SEC (official site for companies Wikidata lacks) |

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
government company registry (gBizINFO) and Wikidata. **For Japanese companies,
the company's own domain given as its method of public notice in its annual
securities report takes precedence** where there is one (other sources
sometimes hold a page that is not the company's own).

Japanese companies missing from both are filled in, in this order:

- the URL the company itself gives as its method of public notice in its
  annual securities report (obtained through the EDINET API). Some companies
  have a service provider post their notices, so it is used **only when that
  page carries the Japanese trade name**
- an address built from the English trade name (`.co.jp`, then `.jp` and
  `.com`), used **only when that page carries the Japanese trade name**
- the official site in the infobox of the Japanese Wikipedia article. **The
  article is confirmed to be about the same company by the securities code in
  its listing field**, and a domain already assigned to another company is not
  used

For US companies, the site the company states in its own annual report takes
precedence over Wikidata where the two differ (Wikidata sometimes still holds the
site from before a name change); the earlier site is kept as an additional
candidate domain. For US companies that
Wikidata lacks, the site is read from the annual report filed with the SEC
(10-K / 20-F / 40-F), **only where the company itself states "our website is
…"**. Other URLs in the document (the SEC, auditors, business partners) are
not used. Newly listed companies with no annual report yet, and companies whose
annual report does not state one, are read the same way from their prospectus
(S-1 / F-1 / 424B4 and similar) and their proxy statement (DEF 14A). Filings
made before the company's last name change and merger proxy statements are not
used. For companies where none states one, the "About"
section and contacts in their press releases (8-K / 6-K) are read, and **only
a domain matching the company name** is used (contacts are often investor
relations agencies). Only the domain part of email addresses is used; the
addresses themselves are not stored. Some companies are still left without one. **Nothing that cannot be confirmed is used** — measuring another company's domain by
mistake is a worse error than not measuring at all.

### Nikkei 225 constituents

The Nikkei 225 constituents (`jp-nikkei225`) are counted separately **from the
Japan-listed measurement (`jp-all-listed`) without measuring again**.

- The constituents list uses only the securities codes from the "constituents"
  section of the Japanese Wikipedia article
  "[日経平均株価](https://ja.wikipedia.org/wiki/%E6%97%A5%E7%B5%8C%E5%B9%B3%E5%9D%87%E6%A0%AA%E4%BE%A1)"
  (CC BY-SA 4.0), as of 1 October 2026
- Nothing is retrieved from Nikkei's index website (its terms of use prohibit
  storing its information or providing it to third parties without permission)
- The copyright of the Nikkei Stock Average belongs to Nikkei Inc. This site
  neither calculates nor displays index values

### Group companies (domestic subsidiaries)

Domestic subsidiaries of Japan-listed companies are measured as **a population
separate from the listed companies** (`jp-all-listed-group`). A subsidiary's
mail is not always run by its parent, so these are not mixed into the
listed-company figures.

- Subsidiary names are those the company gives under "affiliated companies" in
  its annual securities report (EDINET). The website domain comes from the
  company homepage registered in METI's gBizINFO corporate data. **Only names
  matching exactly one corporation are used** (so that a different company with
  the same name is not measured)
- Listed subsidiaries (measured as listed companies) and subsidiaries whose
  website is on a listed company's own domain are left out
- **This is not every subsidiary.** It covers the main subsidiaries named in the
  annual securities report that have a website registered in gBizINFO. Overseas
  subsidiaries are not included
- Group companies of the Nikkei 225 constituents (`jp-nikkei225-group`) are those
  in this population whose parent is a constituent, counted separately

## Companies left out of the population

**Entities that are not operating companies** are left out of the US
population. They have no staff or mail servers of their own and normally use
their manager's or sponsor's mail infrastructure. Keeping them would either
lower the rates as companies with no starting domain, or pile dozens of them
onto the manager's domain so that **one configuration is counted as dozens of
"companies"**.

The classification uses the SEC's own categories and, as a rule, not words in
the name ("Trust" also appears in the names of banks and real estate
investment trusts).

| Category | Rule |
|---|---|
| SPAC (blank-check company) | SEC industry code 6770, or a name of the form "… Acquisition Corp" (the SEC sometimes assigns the target's industry code) |
| Commodity or crypto trust | Industry code 6221 and a name containing ETF / Trust / Fund |
| Royalty or structured trust | Industry code 6189 (asset-backed), or 6792 with Trust in the name |
| Fund, ETF or closed-end fund | Files investment-company forms (N-CSR, N-PORT, N-2 and so on) and no 10-K / 10-Q / 20-F |

Business development companies (BDCs) file 10-Ks and are kept.

**These were deliberately not measured; they are not "could not be measured".**
They are not counted in the "N companies" of the summary. Their names and the
reason are listed below (with no measurement values).

```js
const excludedEntities = FileAttachment("../data/excluded_entities.json").json();
```

```js
{
  const labels = {
    spac: "SPAC (blank-check company)",
    commodity_trust: "Commodity or crypto trust",
    registered_fund: "Fund, ETF or closed-end fund",
    passive_trust: "Royalty or structured trust",
  };
  const rows = (excludedEntities ?? []).map((r) => ({...r, category: labels[r.reason] ?? r.reason}));
  if (!rows.length) {
    display(html`<p>No companies were left out this month.</p>`);
  } else {
    const counts = d3.rollups(rows, (v) => v.length, (r) => r.category);
    display(html`<ul>${counts.map(([k, n]) => html`<li>${k}: ${n.toLocaleString("en-US")}</li>`)}</ul>`);
    display(html`<details><summary>Companies left out (${rows.length.toLocaleString("en-US")}, ${rows[0].measured_month})</summary>${
      Inputs.table(rows, {
        columns: ["name", "ticker", "exchange", "category"],
        header: {name: "Name", ticker: "Ticker", exchange: "Exchange", category: "Category"},
        sort: "name",
        rows: 20,
      })
    }</details>`);
  }
}
```

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
| Outbound front-end | products that outgoing mail passes through (counted from SPF traces; may be the same product as the inbound front-end) |

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
| Official site | the official URL from government registries, Wikidata, Japanese Wikipedia (checked by securities code), US annual reports, prospectuses, proxy statements and press releases (as stated by the company), and Japanese sites confirmed by the trade name on the page |
| CT logs | names appearing in Certificate Transparency logs |
| Links on the official site | other domains linked from the official site's top page, **where that page's title carries the company's trade name and the domain has MX**. Domains linked by many companies (social media, exchanges and so on) are not used |
| Same name, another TLD | for companies whose website domain has no MX, the same name under another TLD (such as `canon.co.jp` for `global.canon`), **where the domain has MX and its top page title carries the company's trade name**. Some companies use separate domains for their website and their mail |
| Annual securities reports | the company's own domain that a Japanese company gives as its method of public notice in its annual securities report (domains of providers that post notices for others are excluded) |
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
- The routes are limited to those three plus the one below. A subdomain sending
  mail by any other route is not visible here
- From the 2026-12 measurement, **`mail.` and `email.`** under the primary domain
  are also queried. The names are a guess, but as above only those carrying an
  MX or `_dmarc` record of their own are measured. **Where a domain returns an
  MX for any name (a wildcard), it is not counted as the subdomain's own MX.**
  Subdomain counts are not comparable with earlier months
- From the next month in which certificate transparency (CT) logs are queried,
  subdomains with **mail-like names** (`news.`, `mail2.`, `info.` and the like)
  that appear in the CT records for the official site are also candidates. The
  names exist, but again only those carrying an MX or `_dmarc` record of their
  own are measured. Subdomain counts are not comparable with earlier months

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

We add selectors that a provider's own documentation names as fixed. Some
providers, however, use names that cannot be known in advance:

- the selector contains a date or number generated by the admin console
- the customer chooses the name
- the provider signs with its own domain, so no key sits under the customer's
  domain

Companies using these providers fall under "not found among known selectors"
even when their mail is signed. We do not query dates or numbers by brute force.

### MTA-STS mode

Whether the policy is `enforce` or `testing` cannot be determined from DNS.
For domains with an `_mta-sts` TXT record, the policy file
(`https://mta-sts.<domain>/.well-known/mta-sts.txt`) is fetched the same way a
sending mail server would fetch it (RFC 8461: the certificate is validated and
redirects are not followed). This began with the US measurement for 2026-11;
earlier months have no breakdown by mode.

Policies that could not be fetched or read are counted separately as having no
effect, since senders cannot read them either. Each policy is fetched once, so
a temporary outage also lands in this group.

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
