# Changelog

All notable changes to OpenABox. Versions follow [semantic versioning](https://semver.org).
While the major version is 0, the interface may change between minor versions.

## [0.6.0] — 2026-08-16

### Added
- **Table browser.** A *Graph / Table* switch in the header swaps the canvas for
  a sortable, filterable table of anything in the graph: companies, people,
  addresses, cities, establishments, NACE codes, legal forms, legal situations
  and filings — plus **shareholdings** and **directorships**, which are edges
  and so had no home on a canvas that shows one company at a time. Clicking a
  row loads that record onto the graph, so the two views feed each other.
- `GET /api/browse` returns the table registry; `GET /api/browse/{table}`
  returns one page with its total, and `GET /api/browse/{table}/export.csv`
  returns the whole filtered set. The UI builds its tabs, headers and sort
  defaults from the registry rather than hardcoding them, on the same rule as
  the version and licence.
- **Per-column filters** on top of a free-text search across the table. Numeric
  columns accept comparisons (`>25`, `<=100`); a bare number reads as *at
  least*, because the question behind typing 1 into an Owners column is "which
  have any?".
- **CSV export** of exactly the rows on screen — same filters, same sort. Capped
  at 10 000 rows, and the button says which of the two it is offering rather
  than handing over a truncated file that looks complete.
- **Open in console** hands the Cypher the table just ran to the existing
  console, so the table is a way into the query language rather than a
  replacement for it.
- [`api/tests/test_browse.py`](api/tests/test_browse.py) — 22 tests covering
  registry integrity, the rejection of unknown tables, columns and sort keys,
  filter-operator parsing and CSV flattening.

### Notes
- **Every table lists the local cache, not the register.** Each carries a scope
  line saying so, and the companies table exposes `_hydrated` as a column: a
  company known only because someone else's filing named it has to be visibly a
  stub, or 400 rows read as a claim about Belgium.
- Counts that cross a filing-derived edge count **distinct parties, not edges**.
  Ownership edges are merged on `as_of`, so a company that has filed five years
  running carries five `SHAREHOLDER_OF` edges from one owner — a plain edge
  count would report that owner as five.
- Cypher cannot parameterise a label or a property name, so the browse endpoint
  builds its query as a string. Read-only transactions do not close that hole.
  Every entity, column and sort key is therefore resolved against a server-side
  registry and rejected if unknown; only filter *values* travel as parameters.
- **Also in this tag, not yet wired up:** `enterprise_url()` and
  `deposit_urls()` in [`cbso_client.py`](api/app/cbso_client.py), which build
  public NBB Consult links for a filing, with tests. Nothing calls them yet, so
  they change no behaviour — recorded here rather than shipped silently, which
  is what 0.4.0 did.

## [0.5.1] — 2026-08-16

### Added
- **MIT licence.** [LICENSE](LICENSE) holds the full text; the code is provided
  as is, without warranty or guarantee of any kind.
- **Licence footer in the UI.** A colophon under the console bar states the
  copyright, links to the licence, and carries the warranty disclaimer plus a
  note that CBE and NBB register data is not guaranteed accurate, complete or
  current.
- Licence constants live in [`api/app/version.py`](api/app/version.py) next to
  `__version__`, on the same single-source-of-truth rule: `GET /health` reports
  `license`, `license_url`, `copyright` and `disclaimer`, `/docs` renders
  FastAPI `license_info`, and the UI footer fills itself from `/health`. The
  footer's markup carries the MIT terms statically, so a failed health call
  leaves a correct footer standing rather than blanking it.
- A **Licence** section in the README, separating what the MIT licence covers
  (this code) from what it does not (the register data flowing through it).

## [0.5.0] — 2026-08-16

### Added
- **Shareholders, directors and participations, from the NBB.** The CBE register
  has none of this; the annual accounts filed with the National Bank's Central
  Balance Sheet Office have all of it, as structured XBRL.
  `GET /api/company/{cbe}/shareholders` fetches a company's latest filing and
  writes `SHAREHOLDER_OF`, `DIRECTOR_OF`, `HOLDS_PARTICIPATION`,
  `CONSOLIDATED_BY` and `AUDITED_BY` edges. Legal persons carry their CBE
  number in the filing, so they join straight onto existing `Company` nodes —
  no name matching. Parties not already known are created unhydrated, which is
  how the graph grows past the companies explicitly searched for.
- **Right-click investigation in the UI.** Right-click any company node for
  *Investigate shareholders*, *Financials over time*, or *Expand neighbours*.
- **Ownership rendering.** Ownership edges carry direction arrows and are
  captioned with the percentage instead of the relationship name. Ownership is
  solid, control is dashed, and thicker lines mean larger stakes.
- **Financial history panel.** `GET /api/company/{cbe}/financials` returns a
  per-year series — turnover, result, operating result, equity, liabilities,
  total assets, FTE — shown as charts in a right-hand panel, including equity
  vs liabilities with the equity ratio.
- `GET /api/graph/company/{cbe}/ownership` returns just the ownership
  neighbourhood, so an investigation adds that and not the whole graph.
- `Deposit` and `ExternalEntity` node types, with constraints. Every ownership
  edge records the filing it came from.

### Fixed
- **Share counts were being zeroed.** Filings report voting rights twice on the
  same axis, split between rights linked to securities and rights not linked;
  the second is almost always 0. Reading whichever fact arrived last silently
  set every shareholding to zero.
- **Natural persons were splitting into two nodes.** Filers disagree about
  which name field is which — Achilles Dott files surnames in the surname
  dimension, Korys NV filed Willem Colruyt as surname "Willem", first name
  "Colruyt". Person keys now sort the name parts, so one director stays one
  node. This bug shipped in 0.4.0; graphs built with that version should
  re-ingest.
- Treasury shares make a company its own shareholder, which sent
  variable-length ownership queries round in circles and reported the same
  owner at several depths. Self-loops are now excluded from path traversals.
- `/api/company/{cbe}/connections` was querying edge types that never existed
  (`OFFICER_OF`, `FOUNDED`) and so always returned nothing.

### Notes
- **0.4.0 shipped part of this feature undocumented.** The shareholder backend
  was mid-development when that release was cut, so `identity.py`, `ingest.py`,
  `main.py`, `graph.py` and `test_xbrl.py` went out with it — including the
  person-key bug fixed above. 0.4.0's changelog describes only the pagination
  fix. Nothing in 0.4.0 was broken, but it was not what the tag claimed.
- Rubriek codes are **not comparable across filing models**: `9900` is unused
  in the full scheme and means *gross margin* in the abbreviated one, so it is
  never read as the operating result. Only `9901` is.
- Metrics absent from a filing stay absent rather than becoming 0 — abbreviated
  and micro filings do not disclose turnover, and capital-less BVs have no
  capital code. A zero would plot as a real collapse.
- Shareholder disclosure is **not** limited to listed companies, which is what
  this project assumed and had wrong. But coverage is genuinely uneven: Korys
  NV's filing omits the shareholder section entirely. How often the fields are
  populated across a random sample of Belgian filers is still unmeasured — see
  [shareholders.md](shareholders.md).
- The public Consult portal is used without credentials and its terms rule out
  systematic downloading, so ingestion is strictly cache-first. Setting
  `CBSO_SUBSCRIPTION_KEY` switches to the official (free) web services.

## [0.4.0] — 2026-08-16

### Fixed
- **Address and NACE searches were silently returning only the first page.**
  Both upstream endpoints are properly paginated — 25 per page with `total`
  and `last_page` — but the client read `data` once and discarded the rest.
  `/api/nace/62010/companies` was therefore answering with 25 companies out
  of 29,273, presented as if complete. The client now follows pages up to a
  `max_pages` bound (default 4 = 100 records, max 40), and every response
  carries a `pagination` block reporting `total`, `pages_fetched` and
  `truncated`. The UI states "Showing X of Y" whenever the set was cut short.

### Notes on upstream behaviour
- `street` matches as a **prefix**: `street=Edingense` returns the same 145
  results as `street=Edingensesteenweg`.
- Filtering by city name is broader than by post code, because a municipality
  spans several: `street=Edingensesteenweg&city=Halle` gives 145, while
  `post_code=1500` gives 104 (Halle also covers 1501 and 1502).
- Address search, unlike name search, returns a genuine paginator — so it is
  the endpoint to use for exhaustive queries.

## [0.3.0] — 2026-08-16

### Added
- **Click an address to see every company there.** Selecting an `Address` node
  lists its occupants in the sidebar, split into *registered office here* and
  *establishment here* — being registered at an address is a different claim
  from operating a site there, and merging the two would hide it. Each entry
  loads that company's graph, and "Add all to canvas" pulls them in at once.
- Selecting a `City` node lists the companies registered in that postal area.
- `GET /api/address/companies?key=` and `GET /api/city/companies?key=`.

### Changed
- Replaced `GET /api/address/{key}/companies`, which returned bare name lists
  and took the key as a path segment even though keys contain `|`.

## [0.2.1] — 2026-08-16

### Fixed
- The search box was collapsing to a few characters wide. It sat in one flex
  row with the logo, wordmark, version, mode select, button, live checkbox and
  the quota readout, and carried `min-width: 0`, so every other element took
  width from it first. Search now has its own full-width row, a `320px`
  minimum, larger text and a visible focus ring; the quota readout moved up
  beside the brand.
- Address fields no longer shrink below a readable width either.

## [0.2.0] — 2026-08-16

### Changed — breaking
- **`City` is now a node.** `Address` no longer carries `post_code` or `city`;
  they moved onto `(:City)` reached via `(:Address)-[:IN_CITY]->(:City)`.
  Cities are keyed by country + post code, because the register files one post
  code under several names (`1040` is both *Etterbeek* and *Brussel*) while one
  name spans many post codes (Antwerpen has nine). Every spelling seen is kept
  in `City.aliases`, so searching either name finds the node.
- Address search matches the locality against all aliases, and returns the
  city alongside each result.
- Dropped the now-meaningless `address_post_code` index; added `city_post_code`
  and `city_name`.

**Migration:** address keys are unchanged, but existing `Address` nodes keep
stale `post_code` / `city` properties and have no `:IN_CITY` edge. Clear the
graph and re-run your searches:

```bash
docker compose exec neo4j cypher-shell -u neo4j -p openabox-local "MATCH (n) DETACH DELETE n"
```

### Added
- Cardboard-box visual identity: logo, SVG favicon, and a kraft-brown palette
  over a white graph canvas, in both light and dark modes.
- Saved queries for companies per city and post codes filed under several names.

### Fixed
- Address fields appeared in every search mode: `#address-fields` had a
  `display` rule, which outranks the browser's `[hidden]` styling, so the
  attribute had no effect. A global `[hidden] { display: none !important; }`
  now keeps the attribute authoritative everywhere.
- Address inputs have visible labels instead of placeholders truncated by a
  cramped header.

## [0.1.0] — 2026-08-16

First tagged release. Company lookup and graph exploration work end to end
against the live CBE register; shareholder data is not yet ingested.

### Added
- Docker Compose stack: Neo4j 5.26 Community (APOC, persistent volumes) and a
  FastAPI service.
- CBE API client covering all ten endpoints, with rate-limit tracking.
- Graph model with addresses as first-class building-level nodes, so
  shared-premises detection is a traversal rather than a string comparison.
- Address canonicalisation folding case, accents, punctuation, abbreviations,
  compound/split street types and municipality disambiguators (11 unit tests).
- Ingestion recording `_source` / `_fetched_at` provenance and a `_hydrated`
  flag separating "company exists" from "full record held".
- Search by name, CBE/VAT number, NACE code and address — all cache-first, so
  repeat queries do not spend API quota.
- Web UI at `/`: force-directed graph canvas with expand, pin, zoom and pan,
  plus a Cypher console with saved queries. No build step and no CDN.
- Read-only Cypher endpoint, enforced by Neo4j's transaction access mode
  rather than by keyword filtering.
- Reserved edge types for the shareholder work: `HOLDS_PARTICIPATION`,
  `FOUNDED` and `OFFICER_OF`, with `/connections` already querying them.

### Known limitations
- No shareholder, director or financial data — the CBE API does not expose it.
  See the data sources table in the README.
- Upstream name search is capped at 10 results with no pagination.
- Auto search mode resolves a short all-digit term to a NACE code; it is
  ambiguous with a postal code, so use Address mode for the latter.
- The canvas force layout is O(n²); expect it to slow past a few hundred
  visible nodes.
