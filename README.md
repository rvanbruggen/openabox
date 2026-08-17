# OpenABox

A self-hosted Belgian company lookup and graph exploration tool. Data comes from
the CBE/KBO register via [cbeapi.be](https://cbeapi.be), is cached in a Neo4j
instance you run yourself, and is explored from there — the API is not
re-queried for records already held.

Everything runs in two containers on your own machine or network: nothing is
sent anywhere except the register lookups themselves, and the graph never leaves
the host you put it on. See [Installation](#installation) to get it running.

## Version

**0.8.0** — see [CHANGELOG.md](CHANGELOG.md) for what is in it.

The version is defined once, in [`api/app/version.py`](api/app/version.py), and
everything else reads it from there:

| Surface | How it gets the version |
|---|---|
| `GET /health` | `version` field |
| `GET /docs` (OpenAPI) | FastAPI `version` |
| Web UI header | fetched from `/health`, never hardcoded |
| Git | annotated tag `v0.8.0` |

The licence follows the same route — `__license__` in the same file, reported
by `/health`, rendered in `/docs` and in the UI footer.

Nothing duplicates the string, so the UI cannot drift from the backend that is
actually running — if the header says `v0.8.0`, that is the code answering.

## Status

| Component | State |
|---|---|
| Docker Compose stack (Neo4j + API) | Verified |
| CBE API client | Verified against the live API |
| Address + city canonicalisation | 17 unit tests passing; verified on live register data |
| Graph schema + ingestion | Verified — provenance set, shared-address merging confirmed |
| REST + Cypher endpoints | Verified, including cache-first behaviour |
| Web UI (graph canvas + Cypher console) | Verified in a browser |
| Shareholder / director ingestion (NBB annual accounts) | Verified end-to-end on 4 live filings |
| Right-click investigation + ownership rendering | Verified in a browser |
| Financial history panel | Verified on full, abbreviated and micro filings |
| Person search (shareholders, directors) | Verified in a browser against live graph data |
| Table browser (11 tables, filters, CSV) | Routes and query building verified; Cypher not yet run against live data |
| Staatsblad ingestion (changes between filings) | Not started |

Verified on a live ingestion of 10 companies: 336 establishments resolved to
328 addresses, and the five Colruyt entities registered at Edingensesteenweg
196 correctly merged onto a single `Address` node.

Ownership was verified by ingesting four real filings and confirming that two
independent sources agree: Colruyt Group's own accounts say it holds 100 % of
CGMI BV, and CGMI's accounts say Colruyt Group owns 100 % of it. Re-ingesting a
filing leaves the edge count unchanged, so ingestion is idempotent.

## Installation

### What you need

| | |
|---|---|
| Docker Engine with Compose v2.24 or newer | `docker compose version` — v2.24 (Jan 2024) is where the optional `.env` support used here landed |
| ~3 GB of free RAM | Neo4j is configured for a 2 GB heap plus a 1 GB page cache; both are tunable, see below |
| A CBE API key | Free from [cbeapi.be](https://cbeapi.be). Without one the stack still starts, but no new company can be looked up |

Nothing else is needed on the host: there is no Python, Node or build toolchain
to install, and the web UI has no npm or CDN dependencies.

### Getting it running

```bash
git clone https://github.com/rvanbruggen/openabox.git
cd openabox
cp .env.example .env
```

Open `.env` and set at least these two:

- `CBE_API_KEY` — your key from cbeapi.be.
- `NEO4J_PASSWORD` — **change it before the first start.** Neo4j bakes the
  password into the database on its first run, so changing it later means
  wiping the volume as well.

Then bring the stack up:

```bash
docker compose up -d --build
```

The first start takes a minute or so: Neo4j has to initialise its store and
install APOC, and the API waits for the database to report healthy before it
starts. Watch it with `docker compose logs -f`, or check it is up with:

```bash
curl http://localhost:8000/health
```

### Where it lives

| | Default URL |
|---|---|
| Web UI | `http://localhost:8000/` |
| API docs (OpenAPI) | `http://localhost:8000/docs` |
| Neo4j Browser | `http://localhost:7474/` (user `neo4j`, the password from your `.env`) |

Running the stack on a different machine on your network — a NAS, a home
server, a VM — changes nothing but the hostname: use that machine's address or
name in place of `localhost`. The UI calls the API on the same origin it was
served from, so it follows automatically and there is no base URL to configure.

### Configuration

Everything is set through `.env`, which Docker Compose reads automatically.
[`.env.example`](.env.example) documents each one; the ones that matter most:

| Variable | Default | What it does |
|---|---|---|
| `CBE_API_KEY` | — | Key for the CBE/KBO register |
| `NEO4J_PASSWORD` | `openabox-local` | Database password, fixed at first start |
| `OPENABOX_BIND` | `0.0.0.0` | Interface the containers publish on. `127.0.0.1` keeps the stack to the host itself |
| `OPENABOX_API_PORT` | `8000` | Host port for the API and UI |
| `NEO4J_HTTP_PORT` / `NEO4J_BOLT_PORT` | `7474` / `7687` | Host ports for Neo4j |
| `CBSO_SUBSCRIPTION_KEY` | — | Optional NBB web-services key; see [Rate limits](#rate-limits-and-why-the-cache-matters-here) |
| `OPENABOX_CACHE_TTL_DAYS` | `90` | How long a company record is trusted |
| `OPENABOX_CBSO_TTL_DAYS` | `180` | How long ownership and financial data are trusted |
| `OPENABOX_LANG` | `nl` | Language for register labels (`nl`, `fr`, `de`) |
| `NEO4J_HEAP_MAX`, `NEO4J_HEAP_INITIAL`, `NEO4J_PAGECACHE` | `2G`, `1G`, `1G` | Lower these on a small host |

After changing `.env`, apply it with `docker compose up -d`.

### Who can reach it

There is **no authentication in front of the API**, and by default the ports
are published on every interface — so anyone who can reach the host can read
the graph and run Cypher against it. That graph contains the home addresses of
private individuals named in annual accounts (see [Identity](#identity)).

Treat it accordingly: keep it on a trusted network, and set
`OPENABOX_BIND=127.0.0.1` if only the host itself needs access. Putting it on
the public internet is not a supported configuration — if you must reach it
remotely, do it over a VPN or behind an authenticating reverse proxy.

### Updating

```bash
git pull
docker compose up -d
```

Application code is bind-mounted, so `docker compose restart api` is enough for
a code-only change; a dependency change in `requirements.txt` needs
`docker compose up -d --build`.

### Stopping and removing

```bash
docker compose down       # stop; the graph is kept
docker compose down -v    # stop and delete the database volumes as well
```

The cached graph lives in the `neo4j-data` volume. Back it up with a Neo4j dump
or by archiving the volume — nothing in it is recoverable from this repo, only
by re-querying the registers.

### Troubleshooting

| Symptom | Cause |
|---|---|
| `Bind for 0.0.0.0:8000 failed: port is already allocated` | Something else holds the port — set `OPENABOX_API_PORT` (or the Neo4j ones) to something free |
| API restarts, logs show it cannot authenticate to Neo4j | `NEO4J_PASSWORD` was changed after the first start. Either put the original back or run `docker compose down -v` and start over |
| Lookups fail with an auth error from the register | `CBE_API_KEY` is missing or wrong; confirm with `docker compose exec api env \| grep CBE` |
| `additional property required is not allowed` on `docker compose up` | Compose is older than v2.24 — upgrade it, or delete the `required: false` line and always keep a `.env` present |
| Neo4j exits during startup on a small host | Lower `NEO4J_HEAP_MAX` and `NEO4J_PAGECACHE` |

### Tests

The tests are plain scripts with no test-runner dependency — run them
individually, or all at once:

```bash
for t in api/tests/test_*.py; do python3 "$t"; done
```

They need no database, no API key and no network — only Python 3.12, plus
`httpx` for `test_cbso_client.py`. If the host has no suitable Python, run them
in a throwaway container against the same image the API uses:

```bash
docker compose run --rm --no-deps -v ./api/tests:/srv/tests api sh -c 'for t in /srv/tests/test_*.py; do python "$t"; done'
```

`test_address.py` covers address and city canonicalisation, `test_xbrl.py` the
shareholder/director extraction and identity keys, `test_financials.py` the
rubriek-code metrics, and `test_browse.py` the table query building — including
that an unknown column or sort key is rejected rather than interpolated.

## Architecture

Two containers on your host, three read-only sources on the internet, and one
rule connecting them: **a remote source is asked once, and everything after
that is answered from your own graph.**

```
  your machine / network                        the internet
  ┌───────────────────────────────────────┐     ┌──────────────────────────┐
  │  Browser                              │     │  cbeapi.be               │
  │   static UI, no build step, no CDN    │     │   CBE/KBO register       │
  │            │ same-origin fetch        │     │   identity · address     │
  │            ▼                          │     │   NACE · form · status   │
  │  ┌─────────────────────────────┐      │     └──────────────────────────┘
  │  │  API container (FastAPI)    │──────┼────────────────▲   Bearer key
  │  │   cbe_client · cbso_client  │      │                    quota-limited
  │  │   xbrl · financials         │      │     ┌──────────────────────────┐
  │  │   ingest · browse           │──────┼────▶│  NBB Central Balance     │
  │  └─────────────────────────────┘      │     │  Sheet Office            │
  │            │ Bolt                     │     │   shareholders·directors │
  │            ▼                          │     │   participations·figures │
  │  ┌─────────────────────────────┐      │     └──────────────────────────┘
  │  │  Neo4j container            │      │
  │  │   the cache and the answer  │      │     Nothing else is contacted.
  │  │   volume: neo4j-data        │      │     Nothing is ever sent out
  │  └─────────────────────────────┘      │     except the lookup itself.
  └───────────────────────────────────────┘
```

The browser talks only to the API container, on the origin it was served from —
there is no base URL to configure and no third-party asset to fetch. The API
container is the only thing that talks to the outside world.

### What each source supplies

| Source | Auth | Supplies | Does **not** supply |
|---|---|---|---|
| **cbeapi.be** (CBE/KBO) | Bearer key, quota-limited | Company identity, addresses, establishments, NACE codes, legal form and status | Any ownership, officers or financials |
| **NBB CBSO** — Consult | none | Shareholders, directors, participations, auditor, balance sheet and P&L, as filed | — |
| **NBB CBSO** — web services | subscription key | The same, sanctioned for systematic use | — |

The split matters: the register says a company *exists* and where; the filings
say who *owns* it and how it is doing. Neither knows what the other holds, and
the graph is where they meet — on the CBE number, which both use.

### How a lookup flows

Every read path is cache-first, and the two caches expire independently
because the underlying data changes at different rates.

| Step | What happens |
|---|---|
| 1 | The UI calls the API. Nothing is fetched remotely yet. |
| 2 | The API asks Neo4j. A hit that is still inside its TTL is returned, tagged `source: cache`. |
| 3 | On a miss — or when **live** is ticked — the remote source is called. |
| 4 | The response is written to Neo4j with provenance, then returned, tagged with the source it came from. |

Ticking **live** skips step 2 for register lookups. It does not skip people or
shareholdings: those exist only in filings already ingested, so there is no
register to re-ask.

### How data enters and is refreshed

Ingestion is `MERGE`-based throughout, which is what makes re-running it safe.
The uniqueness constraints in [`graph.py`](api/app/graph.py) are what make
`MERGE` idempotent rather than duplicating nodes.

| Property | Set on | Meaning |
|---|---|---|
| `_source` | every node | `cbeapi` or `nbb-cbso` — which system said so |
| `_fetched_at` | every node | when this app last wrote it |
| `_hydrated` | `Company` | `true` = full record fetched; `false` = named by someone else's filing and not yet looked up |
| `_cbso_fetched_at` | `Company` | when ownership was last pulled, separate from the register TTL |
| `as_of` | ownership edges | the filing's period end — **part of the merge key** |

Two consequences worth knowing:

- **`_hydrated` is what lets the graph grow.** A shareholder named in a filing
  becomes a `Company` node immediately, marked unhydrated. It is a stub with a
  name and a CBE number until someone looks it up, at which point it fills in.
  Without the flag, "we have no establishments for this company" and "we have
  never fetched this company" would be indistinguishable.
- **Ownership edges are keyed by `as_of`, so history accumulates.** Re-ingesting
  the same filing updates that year's edge; ingesting the next year's filing
  adds one. Merging without the date would overwrite 2019's shareholders with
  2025's and silently destroy the history.

TTLs: `OPENABOX_CACHE_TTL_DAYS` (default 90) for register records, which change
slowly; `OPENABOX_CBSO_TTL_DAYS` (default 180) for ownership and financials,
which change once a year when accounts are filed. `?refresh=true` overrides
either.

### Reading it back

Three ways out of the same graph, all read-only:

- The **graph canvas** — a company's neighbourhood, expanded a node at a time.
- The **table browser** — any entity or edge type as sortable, filterable rows,
  with CSV export.
- The **Cypher console** — arbitrary queries, run in a Neo4j **read
  transaction**, so read-only is enforced by the database rather than by
  pattern-matching for dangerous keywords.

The browse endpoint builds its Cypher as a string, because Cypher cannot
parameterise a label or property name. Every entity, column and sort key is
therefore checked against a server-side registry and rejected if unknown; only
filter *values* travel as parameters.

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

In the UI this is a **right-click on any company node**. The three actions
differ in what they cost and what they change, which the menu states under each:

| Action | Reaches the NBB? | Changes the graph? |
|---|---|---|
| *Investigate ownership* | yes — the latest filing | yes — adds owners, directors, participations |
| *Financials over time* | yes — several years of figures | no — opens the side panel only |
| *Expand neighbours* | no | draws what the graph already holds |

A party with no CBE number cannot be looked up at the NBB, so the first two are
disabled with the reason rather than offered and failing.

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

### Citing the source

Every figure in that panel is derived — extracted from a filing, converted,
sometimes divided by another figure. So the panel links back to the documents it
was derived from, and the table carries a **Source** column putting each year one
click from the accounts it came from:

| Where | What it links to |
|---|---|
| `Source` column, per row | that year's annual accounts as published (PDF) |
| `Sources` block | every filing behind the charts, as **PDF**, **XBRL** and **CSV**, with its NBB reference number |
| Footer link | the company's full filing list on the NBB Consult portal |
| Shareholder panel | the filing the parties were read from |

PDF is the document as published; CSV is the flattened rubriek codes this app
actually parses. Both are offered because they answer different questions —
"what does the filing say?" and "what did OpenABox read?".

The reference number (`2025-00539072`) is shown next to each entry because it
identifies a filing where a URL cannot: quoting a figure in writing, for
instance.

Links are built by `enterprise_url()` and `deposit_urls()` in
[`cbso_client.py`](api/app/cbso_client.py), and returned as `source_urls` on
`/api/company/{cbe}/financials` and `/api/company/{cbe}/shareholders`.

**A filing is not always linkable.** The two NBB backends identify the same
document differently: the Consult portal addresses it by GUID, the official web
services by reference number, and only the GUID resolves on the public portal.
Where the identifier is a reference number, the row shows "no public link" and
falls back to the company's Consult page rather than emitting a URL that 404s.
A dead citation link is worse than no link — it implies the source was checked.

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
to keep an instance unexposed — see [Who can reach it](#who-can-reach-it).

## Tables

The canvas answers *how is this connected?*. The **Graph / Table** switch in the
header answers the other half — *what is in here at all?* — as sortable,
filterable tables of every label in the graph, plus two that are relationships
and so have no home on a canvas showing one company at a time:

| Table | Row |
|---|---|
| Shareholdings | owner → company, stake %, shares, as of |
| Directorships | officer → company, role, represented by, as of |

Clicking a row loads that record onto the graph, so the two views feed each
other rather than competing. **Open in console** hands over the exact Cypher the
table just ran, and **Export CSV** downloads precisely the rows on screen —
same filters, same sort.

**Every table lists the local cache, not the register.** The CBE holds around
two million companies; this graph holds the ones that have been looked up plus
the stubs other companies' filings named. Each table therefore carries a scope
line saying so, and Companies exposes `_hydrated` as a **Full record** column: a
stub has to be visibly a stub, or four hundred rows read as a claim about
Belgium.

Two things the tables get right that a naive version would not:

- **Counts crossing a filing-derived edge count distinct parties, not edges.**
  Ownership edges are merged on `as_of`, so a company that has filed five years
  running carries five `SHAREHOLDER_OF` edges from the same owner. That
  accumulation is the point of the model, and it means a plain edge count
  reports one owner as five.
- **Column keys are resolved server-side, never interpolated.** Cypher cannot
  parameterise a label or a property name, so [`browse.py`](api/app/browse.py)
  builds its query as a string — and a read transaction does *not* close that
  hole, since a read query can still walk the whole store. The client sends
  keys; the server looks up the expression it wrote itself and rejects anything
  unknown with a 400. Only filter *values* travel as parameters.

Numeric filters accept comparisons (`>25`, `<=100`). A bare number reads as *at
least*, because the question behind typing 1 into an Owners column is "which
have any?", not "which have precisely one?".

```
GET /api/browse                     # the registry: tables, columns, defaults
GET /api/browse/companies?q=colruyt&sort=shareholders&dir=desc&f.status=AC
GET /api/browse/shareholdings/export.csv?f.pct=>=25
```

The UI builds its tabs, headers and sort defaults from the registry rather than
hardcoding them — the same rule as the version and the licence, so a column
added in `browse.py` appears in the browser without a second edit.

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

Deploying a release on whichever host runs the stack is the same two commands
as any other update — see [Updating](#updating).
