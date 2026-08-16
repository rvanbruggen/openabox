# OpenABox

A personal, local-only Belgian company lookup and graph exploration tool. Data
comes from the CBE/KBO register via [cbeapi.be](https://cbeapi.be), is cached in
a local Neo4j instance, and is explored from there — the API is not re-queried
for records already held.

## Version

**0.5.1** — see [CHANGELOG.md](CHANGELOG.md) for what is in it.

The version is defined once, in [`api/app/version.py`](api/app/version.py), and
everything else reads it from there:

| Surface | How it gets the version |
|---|---|
| `GET /health` | `version` field |
| `GET /docs` (OpenAPI) | FastAPI `version` |
| Web UI header | fetched from `/health`, never hardcoded |
| Git | annotated tag `v0.5.1` |

The licence follows the same route — `__license__` in the same file, reported
by `/health`, rendered in `/docs` and in the UI footer.

Nothing duplicates the string, so the UI cannot drift from the backend that is
actually running — if the header says `v0.5.1`, that is the code answering.

## Status

| Component | State |
|---|---|
| Docker Compose stack (Neo4j + API) | Running |
| CBE API client | Verified against the live API |
| Address + city canonicalisation | 17 unit tests passing; verified on live register data |
| Graph schema + ingestion | Verified — provenance set, shared-address merging confirmed |
| REST + Cypher endpoints | Verified, including cache-first behaviour |
| Web UI (graph canvas + Cypher console) | Verified in a browser |
| Shareholder / director ingestion (NBB annual accounts) | Verified end-to-end on 4 live filings |
| Right-click investigation + ownership rendering | Verified in a browser |
| Financial history panel | Verified on full, abbreviated and micro filings |
| Staatsblad ingestion (changes between filings) | Not started |

Verified on a live ingestion of 10 companies: 336 establishments resolved to
328 addresses, and the five Colruyt entities registered at Edingensesteenweg
196 correctly merged onto a single `Address` node.

Ownership was verified by ingesting four real filings and confirming that two
independent sources agree: Colruyt Group's own accounts say it holds 100 % of
CGMI BV, and CGMI's accounts say Colruyt Group owns 100 % of it. Re-ingesting a
filing leaves the edge count unchanged, so ingestion is idempotent.

## Running it

On the Docker host (192.168.68.78):

```bash
cp .env.example .env    # then fill in CBE_API_KEY
docker compose up -d
```

- API: `http://192.168.68.78:8000/docs`
- Neo4j Browser: `http://192.168.68.78:7474`

The tests are plain scripts with no test-runner dependency — run them
individually, or all at once:

```bash
for t in api/tests/test_*.py; do python3 "$t"; done
```

`test_address.py` covers address and city canonicalisation, `test_xbrl.py` the
shareholder/director extraction and identity keys, and `test_financials.py` the
rubriek-code metrics.

## Graph model

```
(Company)-[:REGISTERED_AT]->(Address)-[:IN_CITY]->(City)
(Company)-[:HAS_ESTABLISHMENT]->(Establishment)-[:LOCATED_AT]->(Address)
(Company)-[:HAS_ACTIVITY {classification, nace_version}]->(NaceCode)
(Company)-[:HAS_FORM]->(JuridicalForm)
(Company)-[:HAS_SITUATION]->(JuridicalSituation)

# from NBB annual accounts (see "Ownership" below). Every edge carries
# as_of, _source and _deposit, and is MERGEd on as_of so filings for
# different years accumulate instead of overwriting each other.
(Company|Person)-[:SHAREHOLDER_OF {pct, shares, share_nature, as_of}]->(Company)
(Company|Person)-[:DIRECTOR_OF {role_label, represented_by, as_of}]->(Company)
(Company)-[:HOLDS_PARTICIPATION {pct, pct_via_subsidiaries, equity, result}]->(Company)
(Company)-[:CONSOLIDATED_BY {as_of}]->(Company)
(Company)-[:AUDITED_BY {role, membership_number, as_of}]->(Company)
(Person)-[:RESIDES_AT]->(Address)
(Company)-[:FILED]->(Deposit)
```

**Addresses are nodes, not properties.** That is what makes "which companies
share this building?" a graph traversal rather than a string comparison. The
key is building-level — box/unit numbers live on the establishment — so
companies in different units of the same building still meet on one node.

**Cities are keyed by country + post code**, not by name, and `Address` no
longer carries `post_code` or `city` at all. Both halves of that decision come
from the live register:

| Observation | Consequence |
|---|---|
| One post code carries several names — `1040` is filed as both *Etterbeek* and *Brussel* | Keying on the name would split one locality into two nodes |
| One name spans many post codes — Antwerpen has nine | Keying on the name alone would merge distinct postal areas |
| The register holds seven countries (BE, LU, IN, NL, DE, FR, UK) | Country has to be part of the key |

Every spelling seen for a post code is kept in `City.aliases`, so a search for
either *Etterbeek* or *Brussel* finds the same node. `Address.full_address`
still holds the register's raw display string, post code and all.

## Ownership

The CBE API provides identification, addresses, establishments, NACE codes,
legal form and status. It provides **no shareholder, director or financial
data**. All of that comes from the annual accounts filed with the National
Bank's Central Balance Sheet Office, fetched per company by
[`cbso_client.py`](api/app/cbso_client.py) and extracted by
[`xbrl.py`](api/app/xbrl.py):

```
GET /api/company/{cbe_number}/shareholders
```

One filing yields shareholders (legal *and* natural persons, with percentages
and share counts), the board, the auditor, and the participations the company
holds in others — with **CBE numbers attached to legal persons**, so they join
straight onto existing `Company` nodes rather than being matched on name.

Two things about the source shape the code:

- **The CBSO taxonomy is fully dimensional.** A 1.8 MB filing uses about
  eighteen element names; all meaning lives in the context dimensions. There is
  no `<Shareholder>` tag. The dimension constants in `xbrl.py` are taken from
  NBB's own label linkbases, not guessed.
- **Percentages are filed as fractions** (`0.6444`) and converted to percent
  once, at extraction, so nothing downstream has to remember which it holds.

### Reading it on the canvas

In the UI this is a **right-click on any company node** → *Investigate
shareholders*, *Financials over time*, or *Expand neighbours*. A party with no
CBE number cannot be looked up at the NBB, so the option is disabled with the
reason rather than offered and failing.

Ownership edges carry direction arrows — for ownership, which way the edge
points is the whole question — and are captioned with the percentage rather
than the relationship name:

| | |
|---|---|
| Solid orange, arrow | owns (`SHAREHOLDER_OF`) |
| Solid green, arrow | holds stake in (`HOLDS_PARTICIPATION`) |
| Dashed purple | directs — control without ownership |
| Dotted blue | consolidated by |
| Line thickness | >50 % control, 25–50 % blocking minority |

Ownership is solid and control is dashed on purpose: a director controls
without owning, and conflating the two is the mistake the colouring exists to
prevent. Foreign parties render as a distinct label, since `:Company:ExternalEntity`
would otherwise be indistinguishable from a Belgian company.

### Financial history

The same filings carry the balance sheet and P&L. `GET /api/company/{cbe}/financials`
returns a per-year series, and the UI shows it in a right-hand panel: turnover
and result, equity vs liabilities with the equity ratio, and operating result.

The figures come from the NBB's own CSV export of the filing, which flattens
the XBRL to standard Belgian **rubriek codes** — a documented numbering scheme
is a more stable contract than the dimensional encoding. Two traps, both
handled in [`financials.py`](api/app/financials.py) and covered by tests:

- **Rubriek codes are not comparable across filing models.** Code `9900` is
  unused in the full scheme but means *gross margin* in the abbreviated one, so
  it is never read as the operating result. Only `9901` is.
- **A metric that was not filed must stay absent, never become 0.** Abbreviated
  and micro filings do not disclose turnover, and capital-less BVs have no
  capital code at all. A zero would plot as a real collapse.

Consolidated filings are excluded from the series: they restate the whole group
and would otherwise sit alongside the company's own figures as if they were one
continuous history.

### Coverage

Shareholder disclosure is *not* limited to listed companies, which is the
assumption this project started with and had wrong. Verified on live filings:

| Company | Model | Extracted |
|---|---|---|
| Colruyt Group NV `0400378485` | full | 11 shareholders, 10 directors, 33 participations |
| CGMI BV `0779301067` | abbreviated | sole shareholder + CBE, director, auditor |
| Achilles Dott BV `0691752926` | abbreviated | 4 legal + 5 natural-person shareholders |
| Korys NV `0844198918` | full | **no shareholders at all**; 3 directors, 9 participations |

That last row is the honest counterweight to the other three. Korys NV is a
private holding, and its filing simply omits the shareholder section — no
`snlp`/`snnp` dimensions, no `psn:m20`/`m22` members. The extractor is not
missing them; they are not there. The UI reports "the filing names no
shareholders" rather than an empty list that reads like a failure.

**Not yet measured:** how often these fields are populated across a random
sample of Belgian filers. Three of the four verified companies are in one
group, which tends to file carefully. See [shareholders.md](shareholders.md)
for the full source research, the endpoint contracts and the open questions.

Other sources considered: the **UBO register** is not an option (public access
withdrawn after the 2022 CJEU ruling); **eStox** is notary-only; the
**Staatsblad** remains useful for changes between filings and is not
implemented.

### Rate limits, and why the cache matters here

The credential-free Consult portal states it "is not intended for the
systematic - or mass - consultation or downloading of files". The endpoint is
cache-first for that reason as much as for speed: the NBB is asked once per
company, then the graph answers. Setting `CBSO_SUBSCRIPTION_KEY` switches
[`cbso_client.py`](api/app/cbso_client.py) to the official web services, whose
"Authentic Data Query" product is free of charge and is the right basis for any
scheduled ingestion.

### Identity

Legal persons carry a CBE number, so they need no matching. Natural persons
carry a name and nothing else, so a key is constructed in
[`identity.py`](api/app/identity.py): normalised name plus the home post code
where the filing gives one, else the CBE number of the company the mandate is
held in. **Two people are never merged on name alone** — "Jan Peeters" is not
one person — so the fallback deliberately confines a person to a single
company rather than risking a false link between unrelated businesses.

The two name parts are **sorted** into the key, because filers disagree about
which field is which: Achilles Dott files surnames in the surname dimension,
while Korys NV filed Willem Colruyt as surname "Willem", first name "Colruyt".
Without sorting, one director becomes two nodes and the cross-company link
disappears. Display names keep the filing's own order, so a person can appear
as "Colruyt Willem" — that is the filing being wrong, not the graph.

Filings contain private individuals' home addresses. That is a further reason
to keep this instance unexposed, as the brief intends.

## Notes from probing the live API

- **Cloudflare blocks default library User-Agents** with HTTP 403 (error 1010)
  before the request reaches the API. This looks identical to an auth failure.
  The client sends a browser User-Agent for this reason.
- **Quota is 2500 requests per ~11h window**, reported via `x-ratelimit-*`
  headers on every response.
- **Name search is capped at 10 results with no pagination**, despite the spec
  documenting a paginated envelope. Anything exhaustive needs the bulk CBE
  Open Data dump instead.
- The register includes **foreign entities**, so addresses are not all Belgian.

## Caching

Company nodes carry `_source`, `_fetched_at` and `_hydrated`. The `_hydrated`
flag separates "we know this company exists" from "we have its full record",
which is what stops a cache miss being indistinguishable from a company that
genuinely has no establishments. TTL defaults to 90 days
(`OPENABOX_CACHE_TTL_DAYS`); `?refresh=true` forces a re-fetch.

The Cypher endpoint runs queries in a **read transaction**, so read-only is
enforced by Neo4j itself rather than by keyword blocklisting.

## Licence

OpenABox is released under the **MIT Licence** — see [LICENSE](LICENSE) for the
full text. Copyright © 2026 Rik Van Bruggen.

The software is **provided as is, without warranty or guarantee of any kind**,
express or implied, and the author is not liable for any claim or damage
arising from its use. That is the licence's own wording, and it is meant
literally here: this is a personal tool, run locally, against registers whose
shape it does not control.

The licence covers **this code, and nothing else**. It says nothing about the
data that flows through it:

| | |
|---|---|
| This repository | MIT, as above |
| CBE/KBO register data, via [cbeapi.be](https://cbeapi.be) | Terms of the register and of the API provider |
| NBB annual accounts (Central Balance Sheet Office) | NBB's terms — the credential-free Consult portal rules out systematic or mass downloading |

Neither register guarantees that what it returns is accurate, complete or
current, and neither does this tool: a company's own filing is the only thing
it repeats. Filings also contain private individuals' home addresses, which is
a further reason to keep an instance unexposed — see [Identity](#identity).

The licence is stated in three places, all reading the same constant in
[`api/app/version.py`](api/app/version.py): `GET /health`, the OpenAPI page at
`/docs`, and the UI footer.

## Releasing

1. Bump `__version__` in [`api/app/version.py`](api/app/version.py).
2. Add the release to [CHANGELOG.md](CHANGELOG.md).
3. Commit, tag, and push both together:

```bash
git commit -am "Release vX.Y.Z" && git tag -a vX.Y.Z -m "..." && git push origin main --follow-tags
```

Never push a version bump without its tag, and never tag without pushing —
otherwise the running container reports a version that no commit corresponds to.

The tag **must be annotated** (`git tag -a`). `--follow-tags` pushes annotated
tags only, and it does so silently: a lightweight tag is skipped while the push
still reports success, leaving the release untagged on the remote.

Deploying a release on the Docker host:

```bash
cd openabox && git pull && docker compose restart api
```

Application code is bind-mounted, so a restart is enough; only dependency
changes in `requirements.txt` need `docker compose up -d --build`.
