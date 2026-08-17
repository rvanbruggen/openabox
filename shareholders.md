# Shareholder data for OpenABox — research

Status: **implemented.** This document is the research that preceded the build,
kept as the record of where the data comes from and why the design is what it
is. Every endpoint and field below was verified against live data on
2026-08-16; the sample companies are named so the checks can be repeated.

What shipped, and where it lives now:

| This document proposed | Implemented in |
|---|---|
| Route A, the credential-free Consult API | [`cbso_client.py`](api/app/cbso_client.py) |
| Route B, the official web services | same client, via `CBSO_SUBSCRIPTION_KEY` |
| Dimensional XBRL extraction | [`xbrl.py`](api/app/xbrl.py) |
| Ownership edges keyed by `as_of` | [`ingest.py`](api/app/ingest.py) |
| Constructed keys for natural persons | [`identity.py`](api/app/identity.py) |
| Rubriek-code financials | [`financials.py`](api/app/financials.py) |
| Route C, Staatsblad, for changes between filings | **not implemented** |

Two things this research got wrong, corrected by building it:

- The participations note was expected to be the workhorse. In practice the
  filing form's own identification section carries shareholders for ordinary
  companies, which is a stronger source — and directors come with it, removing
  the need to scrape Staatsblad PDFs for officers.
- Coverage is more uneven than the three verified samples suggested. Korys NV,
  a private holding, files no shareholder section at all. The
  [open question](#open-questions) about fill rates across a random sample is
  still open and still the first thing worth measuring.

## Summary

*(Written before the build.)* The README at the time assumed ownership had to be stitched together from
Staatsblad incorporation deeds (stale) plus NBB participations (rare). That
assumption is wrong, and it is wrong in a useful direction.

**The annual accounts filed with the National Bank carry the shareholder list
as structured XBRL — for ordinary small companies, not just listed ones — and
they carry the board of directors and the auditor in the same file.** One HTTP
fetch per company yields ownership, control and the accountant, with CBE
numbers attached for legal-person shareholders.

That collapses three planned ingestion workstreams into one, and it removes the
need to scrape and parse Staatsblad PDFs for officers.

## 1. Where shareholder information can come from

| Source | Gives | Verdict |
|---|---|---|
| **NBB annual accounts (XBRL)** | Shareholders (legal + natural), % held, share counts, directors, auditor, participations held | **Primary source.** Free, structured, per-company. Verified. |
| Staatsblad publications | Incorporation deeds, `KAPITAAL - AANDELEN`, `ONTSLAGEN - BENOEMINGEN` | Secondary. Fills gaps between filings and covers non-filers. PDF, needs parsing. |
| KBO Open Data bulk | Identification, addresses, NACE, functions | No shareholders. Useful for bulk name resolution only. |
| GLEIF Level 2 | Direct + ultimate accounting parent | Only where both sides hold an LEI — a few thousand Belgian entities. Cheap to add, thin. |
| FSMA transparency notifications | Stakes >5% in listed companies | ~120 issuers. Negligible marginal value once NBB is in. |
| UBO register | Beneficial owners | **Unavailable.** Public access withdrawn after the 2022 CJEU ruling; requires demonstrated legitimate interest. AMLR reopens a narrowed access route, but not to a hobby project. |
| eStox (electronic share register) | Authoritative, current share register | **Unavailable.** Notary/accountant access only. |
| Companyweb / Graydon / Bel-first / OpenTheBox | Everything, cleaned | Paid, and licences forbid rebuilding a competing database. Out of scope. |

The two that matter are NBB and Staatsblad. Everything else is either closed or
already covered.

### Why the NBB source is better than expected

There are two distinct shareholder disclosures in a Belgian filing, and they are
easy to conflate:

- The **cross-participation note** (`VOL 5.7` / `VKT 5.3`, "structuur van het
  aandeelhouderschap") is mandatory only for listed companies, companies with
  cross-shareholdings under art. 631–632, and a few similar cases. If this were
  the only route, coverage would indeed be poor — this is what the README's
  pessimism was based on.
- The **identification section of the filing form itself** (`part:m2` /
  "Identificatiegegevens") also carries shareholders, and it is filled in by
  ordinary companies. This is the one that changes the picture.

Verified on three companies of different sizes and models:

| Company | CBE | Model | What the XBRL contained |
|---|---|---|---|
| Colruyt Group NV | 0400378485 | `m02-f` full | 10 shareholders incl. Korys NV 64.44%; 33 participations held |
| CGMI BV | 0779301067 | `m81-f` abbreviated | Sole shareholder Colruyt Group NV 100%, 8,632,014 shares; director Jef Colruyt; auditor EY |
| Achilles Dott BV | 0691752926 | `m81-f` abbreviated | 4 legal-person + 5 natural-person shareholders; 3 directors; accountant; 3 participations |

A small BV disclosing five named natural-person shareholders is the important
data point. That is the common case for the companies OpenABox will actually be
used to look up.

**Caveat, stated plainly:** all three samples are in the Colruyt orbit, and
groups with an audit relationship tend to file carefully. I have *not* measured
how often these fields are populated across a random sample of Belgian filers.
That measurement is the first task before building anything — see
[Open questions](#open-questions).

## 2. How to source it

Two routes to the same data. Use both, for different purposes.

### Route A — Consult portal (no credentials, use for interactive lookups)

The public Consult application at `consult.cbso.nbb.be` is an Angular SPA over a
JSON API that requires no authentication. Endpoints, all verified:

```
GET /api/rs-consult/companies/{cbe}/{LANG}
GET /api/rs-consult/published-deposits?page=0&size=20&enterpriseNumber={cbe}&sort=depositDate,desc
GET /api/external/broker/public/deposits/xbrl/{depositId}
GET /api/external/broker/public/deposits/pdf/{depositId}
GET /api/external/broker/public/deposits/consult/csv/{depositId}
```

Notes from probing:

- Language must be **uppercase** (`/NL`, not `/nl`) — lowercase returns HTTP 500.
- The paginated deposits endpoint **requires** a `sort` parameter; without it,
  HTTP 500. The unsorted `/published-deposits/{cbe}` variant returns 403.
- `/published-deposits/ids?enterpriseNumber=` works and is cheap — useful to
  detect new filings without pulling the full list.
- The CSV export is **numeric rubrieken only** — no names, no shareholders. It
  is not a shortcut; the XBRL is required.
- Deposit listings expose `modelId` (`m02-f` full, `m81-f` abbreviated, `m120-f-p`
  consolidated), `importFileType` (XBRL is only downloadable when this is `XBRL`
  or `ZIP` — older filings are `MICROFILM`/`PDF` and image-only), and
  `periodEndDate`, which is the `as_of` date for every edge extracted.

**The terms of use say this application "is not intended for the systematic — or
mass — consultation or downloading of files", and the NBB reserves the right to
block access without warning.** OpenABox's cache-first design fits inside that
constraint: one fetch per company on first lookup, then served from Neo4j for
the TTL. Do not batch-crawl from this endpoint.

### Route B — official CBSO web services (for anything systematic)

`ws.cbso.nbb.be` is the sanctioned route, and the **"Authentic Data Query"
product is free of charge**:

```
GET https://ws.cbso.nbb.be/authentic/legalEntity/{cbe}/references
GET https://ws.cbso.nbb.be/authentic/deposit/{ref}/accountingData     # Accept: application/xbrl | json | pdf
GET https://ws.cbso.nbb.be/extracts/batch/{date}/accountingData       # zip of all filings for a date
```

Headers: `NBB-CBSO-Subscription-Key` and a per-request `X-Request-Id` UUID.
Access needs a signed subscription with the NBB plus a developer-portal account
at `developer.cbso.nbb.be`. The `extracts` batch endpoint is what makes
continuous enrichment possible — pull one day's filings, ingest everything that
touches a company already in the graph.

**Recommendation:** prototype on Route A, and start the Route B paperwork in
parallel, since it is free and is the only defensible way to run scheduled
ingestion.

### Route C — Staatsblad, for the gaps

Search by CBE number works with a plain GET and needs no session:

```
https://www.ejustice.just.fgov.be/cgi_tsv/rech_res.pl?language=nl&btw={cbe_no_dots}&liste=Lijst
```

The result list is HTML, but each entry is **already typed** —
`OPRICHTING`, `KAPITAAL - AANDELEN`, `ONTSLAGEN - BENOEMINGEN`, `STATUTEN` —
with a date and publication reference. That means the subject tags alone can
drive a cheap "has anything ownership-relevant happened since the last annual
accounts?" check, without downloading a single PDF. Only fetch and parse the PDF
when a `KAPITAAL - AANDELEN` or `OPRICHTING` entry is newer than the latest
filing already ingested.

This is a phase-2 refinement, not a prerequisite.

## 3. Integrating it into the data model

### How the XBRL actually works

The CBSO taxonomy is **fully dimensional**. The whole 1.8 MB Colruyt instance
uses only 18 distinct element names — generic metrics like `met:pct1`,
`met:str2`, `met:int2` — and *all* meaning comes from the dimensions on the
context. There is no `<Shareholder>` tag to grep for. Extraction means resolving
every fact's `contextRef` to its dimension set, then grouping facts by the typed
dimension that names the party.

The party-naming typed dimensions (labels taken from `dim-label.xml` in the
official taxonomy package, not guessed):

| Dimension | Official label |
|---|---|
| `dim:snlp` | Shareholder name — legal person |
| `dim:snnp` / `dim:sfnp` | Shareholder name / firstname — natural person |
| `dim:anlp` | Administrator name — legal person |
| `dim:annp` / `dim:afnp` | Administrator name / firstname — natural person |
| `dim:ptpn` | Participant name (participations held) |
| `dim:aclp` / `dim:cprn` / `dim:cprf` | Auditor — legal person / representative |
| `dim:sanl` / `dim:sprn` / `dim:sprf` | Accountant / tax advisor |

And the attribute coordinates within each party:

| Coordinate | Meaning |
|---|---|
| `bas:m26` + `qlt:m1` | **Company registration number** — the CBE number. This is the join key. |
| `bas:m31` + `ctc:m1…m6` | Street / house number / postbox / postal code / city / country |
| `bas:m117` | "Social rights" — the `pct1` fact here is the ownership percentage |
| `bas:m71` + `spec:m88` | Voting rights — number of shares held |
| `bas:m30` | Legal form; `bas:m37` equity; `bas:m44` result |
| `spec:m87` | Held directly |
| `spec:m32` | "Held by subsidiaries" |
| `spec:m88` / `spec:m89` | "Linked to securities" / "Not linked to securities" — share counts are filed **twice** on the voting-rights axis, and the second is usually 0, so a last-fact-wins read silently zeroes every shareholding |
| `psn:m20` | "Shareholder structure — legal person" |
| `psn:m1` | "Enterprise" (the filer's own participations) |

Percentages are **fractions, not percents** — `0.6444` means 64.44%.

The taxonomy package (`nbb-cbso-26.0.15.zip`, ~61 MB) contains the label
linkbases for every domain. Vendoring the handful of `*-label.xml` files that
matter is worth it: it turns the extractor from reverse-engineered constants
into something that can be re-derived when the taxonomy version changes.

### Proposed graph additions

The existing model already reserves `HOLDS_PARTICIPATION`, `OFFICER_OF` and
`FOUNDED`. Those reservations were shaped around the old assumption and should
be revised — the source now gives a *declared shareholding at a balance-sheet
date*, which is a different and stronger claim than "founded with N shares".

```
(Company)-[:SHAREHOLDER_OF  {pct, shares, share_class, as_of, _source, _deposit}]->(Company)
(Person) -[:SHAREHOLDER_OF  {pct, shares, share_class, as_of, _source, _deposit}]->(Company)
(Company)-[:HOLDS_PARTICIPATION {pct_direct, pct_via_subsidiaries, shares, equity,
                                 result, as_of, _source, _deposit}]->(Company)
(Person) -[:DIRECTOR_OF {role, as_of, _source, _deposit}]->(Company)
(Company)-[:DIRECTOR_OF {role, as_of, _source, _deposit}]->(Company)
(Company)-[:AUDITED_BY  {as_of, ire_number, _source, _deposit}]->(Company)

(:Deposit {id, reference, cbe_number, model_id, period_end, deposit_date})
(Company)-[:FILED]->(Deposit)
```

Three design points that matter:

1. **`SHAREHOLDER_OF` and `HOLDS_PARTICIPATION` point in opposite directions and
   must stay separate.** They are different disclosures with different
   reliability: the shareholder list is what the company says about itself, the
   participations note is what it says about others. Where both exist for the
   same pair they should agree — and where they disagree, that is a signal worth
   surfacing, not an error to silently reconcile.
2. **A `Deposit` node is worth the extra hop.** Every edge is a claim made in one
   specific filing at one specific date. Hanging edges off a `Deposit` gives
   provenance for free, makes re-ingesting a corrected filing a matter of
   detaching one node, and lets the UI answer "where does this number come
   from?" with a link to the source PDF.
3. **Edges must be keyed by `as_of`, not merged blindly.** A company's
   shareholders change. `MERGE` on `(a)-[:SHAREHOLDER_OF]->(b)` without the
   period-end date in the key will silently overwrite 2019's ownership with
   2025's and destroy the history that makes the graph interesting.

`Person` already has a `key` constraint, so no migration is needed there.

## 4. Finding it reliably for companies we have looked up

This is the part where the design decisions actually bite.

### Legal persons — solved

`bas:m26 + qlt:m1` gives the CBE number directly, for Belgian shareholders,
Belgian directors and Belgian participations. That is an exact join onto the
existing `Company.cbe_number` constraint. No fuzzy matching, no name
normalisation. Verified: CGMI BV's shareholder resolves to `0400378485`, which
is Colruyt Group NV's node.

Two cases need handling:

- **Foreign entities** carry a national identifier in the same field with
  different formatting (`CHE-109.996.904`, `NO 0917351538`). These should become
  `Company` nodes keyed on country + identifier, never merged into the CBE
  namespace.
- **Shareholders not yet in the graph** should be created as unhydrated
  `Company` nodes — the existing `_hydrated` flag already models exactly this,
  and it means a shareholder discovered today is a one-click lookup tomorrow.
  This is how the graph grows past the companies explicitly searched for.

### Natural persons — the hard problem

Natural-person shareholders and directors come as **surname + firstname and
nothing else** — no national register number (correctly, it is not public).
Sometimes a private home address is present, sometimes not.

So `Person` identity has to be constructed, and it will be imperfect:

- Key on normalised `surname|firstname` **plus a discriminator** — home postcode
  where present, else the CBE number of the company the mandate is held in.
- **Never merge two people on name alone across different companies.** "Jan
  Peeters" is not one person. Cross-company merging should be a *suggestion*
  surfaced in the UI ("these 3 Person nodes may be the same individual —
  same name, same postcode"), never an automatic write.
- Keep the raw strings on the node alongside the normalised key, so a better
  matching rule later can be re-run without re-fetching.

The existing address canonicalisation in `api/app/address.py` is directly
reusable for the postcode discriminator, and the same building-level `Address`
node design will make "these two people share a home address" a traversal.

**Privacy note:** these filings contain private individuals' home addresses.
For a self-hosted personal tool that is lawful, but it is a good reason to keep
an OpenABox instance unexposed, and not to add any export feature casually.

### Freshness and completeness

- Every edge is stamped with the filing's `period_end`, so "current" is always
  answerable as "latest `as_of` per pair".
- A company that has never filed, or files image-only (`MICROFILM`/`PDF`), yields
  nothing. Record that explicitly — an `_extraction_status` on the `Deposit` — so
  "no shareholders found" is distinguishable from "not looked yet", exactly as
  `_hydrated` does for companies today.
- Filings appear roughly 7–12 months after year end. For anything more recent,
  the Staatsblad `KAPITAAL - AANDELEN` tag is the only free signal.

## Open questions

Ordered by how much they would change the plan:

1. **What is the actual fill rate of the shareholder section across ordinary
   Belgian companies?** Sample 200–500 random CBE numbers from the KBO Open Data
   dump, pull the latest filing, and count. Everything above assumes the three
   verified samples generalise; this is the one assumption that could still
   overturn the design. Do this first, and do it against Route B if the
   subscription has landed.
2. Do older taxonomy versions (pre-2021 models) use the same dimension
   identifiers? If not, the extractor needs a per-taxonomy mapping table, and
   historical depth gets expensive.
3. Consolidated filings (`m120-f-p`) list group subsidiaries. Worth ingesting as
   a fourth edge type, or redundant with the participations note?
4. How aggressively does the Consult portal rate-limit before the "systematic
   consultation" clause is enforced in practice? Unknown — argues for keeping
   Route A strictly interactive and getting Route B in place.

## Reproducing the verification

```bash
CBE=0691752926
curl -s "https://consult.cbso.nbb.be/api/rs-consult/published-deposits?page=0&size=1&enterpriseNumber=$CBE&sort=depositDate,desc"
curl -s -o out.xbrl "https://consult.cbso.nbb.be/api/external/broker/public/deposits/xbrl/<id-from-above>"
```

Then group facts by `contextRef` → dimensions, and read off `dim:snlp`,
`dim:snnp`/`dim:sfnp` and `dim:ptpn`.
