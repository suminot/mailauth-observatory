---
title: Terms and disclaimer
footer: This site measures conformance to published standards and is not an overall security assessment. Data is <a href="/en/terms">CC0</a>.
---

# Terms and disclaimer

## What this site is

This site measures conformance to published standards and is not an
overall security assessment.

What is published here is what can be read from publicly available DNS
records, together with inferences drawn by matching those records against
dictionaries. It does not assess the email security posture of any company,
nor its information security as a whole.

## Disclaimer

- Observations are based on DNS responses at a particular moment. Settings
  may have changed since
- A DNS response that could not be obtained is not treated as "no record",
  but failed observations cannot be ruled out entirely
- Inferences carry a confidence level, and may still be wrong
- Some configurations cannot be detected at all (see [Methodology](/en/methodology))
- No responsibility is accepted for decisions made using this information

## Licence

The aggregated results (the Parquet files under `gold/` and the JSON and CSV
on this site) are published under **CC0 1.0**.

For the primary sources, follow each provider's own attribution terms.

```js
const meta = FileAttachment("../data/meta.json").json();
display(html`<ul>${(meta.attribution ?? []).map((t) => html`<li>${t}</li>`)}</ul>`);
```

The code is Apache-2.0.

## How the measurement is conducted

- To limit load on authoritative DNS servers, the same name is not queried
  repeatedly, and the query rate is capped
- Data from providers that prohibit scraping is not used. Those sources are
  read through their APIs only
- Email addresses containing personal names are neither collected nor stored.
  For DMARC report destinations, only the domain part is kept

## Domains excluded from measurement

Once a domain is excluded from measurement, no further DNS queries are sent to
it. The number excluded is recorded in each month's run record. **Dropping
domains from the denominator silently would move the rates in a way nobody
could account for.**
