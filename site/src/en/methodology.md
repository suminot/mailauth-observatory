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
| P2 Domain candidates | Widens the candidate set from official sites, CT logs, SPF `redirect=` and DMARC `rua` |
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
