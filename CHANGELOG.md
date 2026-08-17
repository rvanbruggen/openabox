# Changelog

All notable changes to OpenABox. Versions follow [semantic versioning](https://semver.org).
While the major version is 0, the interface may change between minor versions.

## [0.8.0] — 2026-08-16

### Added
- **The search box finds people, not just companies.** Shareholders and
  directors extracted from NBB filings are now searchable by name, listed
  alongside company hits with what each person owns and directs. Selecting one
  puts them on the canvas with their stakes, their mandates and their home
  address. Backed by a new `person_names` full-text index; `GET /api/search`
  gained a `people` array and `GET /api/graph/person?key=` returns one person's
  neighbourhood.
- **An Architecture section in the README** — the local containers and the
  remote sources, a table of what each source supplies and what it does not,
  the cache-first request flow, and how records enter and are refreshed:
  the provenance properties, both TTLs, and why `_hydrated` and `as_of` are
  load-bearing rather than incidental.

### Changed
- **The *live* checkbox says what it does.** A line under the search bar states
  which source the next search will use and what it costs: *cached — answers
  from your own graph, free and instant*, or *live — asks the CBE register
  again and stores the answer. Spends one API call per page of results*. One
  word and a tooltip did not convey that one of the two spends quota.

### Fixed
- **A failed register call discarded person matches.** With *live* ticked and
  the register unreachable, `/api/search` raised rather than returning the
  people the graph could already answer — so searching a director's name
  errored where the same search without *live* returned hits. The upstream
  failure is now reported in a `note` alongside those results, and still raises
  when there is nothing else to show.
- People are searched locally whatever `refresh` says. They come only from
  filings already ingested and no register endpoint serves them, so skipping
  them under *live* would have made them vanish for no reachable reason.
- Someone who both owns and directs the same company was listed against it
  twice in the search results.

## [0.7.0] — 2026-08-16

### Fixed
- **Right-clicking a node expanded it before you chose anything.** The
  `mousedown` handler did not check which button was pressed, so a right-click
  started a drag that `mouseup` then completed as a click — and a click on a
  node means "expand". Opening the context menu therefore fetched and drew the
  node's neighbours on its own. Because the canvas had already changed
  identically before any item was picked, all three menu items appeared to do
  the same thing. They never did: *Investigate ownership* fetches a filing and
  adds owners and directors, *Financials over time* fetches several years and
  only opens the side panel, and *Expand neighbours* draws what the graph
  already holds without touching the network. The handler now ignores
  non-primary buttons; left-click still expands exactly as before.
- Each menu item now carries a sub-label saying what it costs and what it
  changes — two reach the NBB, one is local; two alter the graph, one does not.
  The names alone did not distinguish them, which is what made the bug above
  read as "these are duplicates" rather than "something else is firing".

### Changed
- *Investigate shareholders* is now *Investigate ownership*: it has always also
  returned directors, participations and the auditor.
- [shareholders.md](shareholders.md) is marked **implemented** rather than
  "research complete, nothing implemented", with a table mapping each part of
  the research to the module that now does it, and a note on the two things the
  research got wrong — the participations note being less useful than the filing
  form's own shareholder section, and coverage being more uneven than three
  samples suggested.

### Changed — deployment
- **The repository no longer assumes the machine it was written on.** The README
  gave the author's Docker host by IP address as the place the stack lives, so
  the install instructions only worked for one person. It now documents
  requirements, a clone-and-run install, every configuration variable, network
  exposure, updating, removal and troubleshooting — all against `localhost`,
  with the note that another host on your network differs by hostname only. The
  UI already called the API on its own origin, so no code had a base URL to
  un-hardcode.
- **Host, ports and Neo4j memory are configuration, not constants.**
  `OPENABOX_BIND`, `OPENABOX_API_PORT`, `NEO4J_HTTP_PORT`, `NEO4J_BOLT_PORT`,
  `NEO4J_HEAP_INITIAL`, `NEO4J_HEAP_MAX` and `NEO4J_PAGECACHE` all take their
  previous values as defaults, so an existing deployment is unaffected — but a
  host with a busy port 8000 or less RAM to spare no longer needs the compose
  file edited. `OPENABOX_BIND=127.0.0.1` keeps an instance off the network
  entirely, which the README now recommends where only the host itself needs
  access.
- **`docker compose up` works on a fresh clone with no `.env` present.** The API
  service required that file to exist and refused to start without it; it is now
  declared optional, so the stack comes up and the missing key is reported by
  the lookup that needs it. This requires Docker Compose v2.24 or newer, which
  the README states as a requirement.
- [`.env.example`](.env.example) documents every variable the app reads, grouped
  by what it affects, including the ones that were only discoverable by reading
  `config.py`: `OPENABOX_CACHE_TTL_DAYS` and `OPENABOX_LANG`.

### Added
- `api/.dockerignore`, so local `__pycache__`, virtualenvs and test files stay
  out of the image.
- A **Citing the source** section in the README, documenting the NBB source
  links shipped in 0.6.0 — what each one points at, why both PDF and CSV are
  offered, and why a filing identified by reference number rather than GUID
  shows no link.
- `/api/company/{cbe}/financials` now returns `enterprise_url`.

### Fixed
- **The NBB portal address was hardcoded in the frontend.** `enterprise_url()`
  existed in [`cbso_client.py`](api/app/cbso_client.py) but nothing called it,
  while `app.js` built the same URL from a literal — so the one address that
  `CBSO_CONSULT_URL` was supposed to own lived in two places, and changing the
  configured value would have left the UI pointing at the old host. The link is
  now built server-side and passed through, on the same rule as the version,
  the licence and the browse registry. Absent from an older cached response, the
  Sources block omits the footer link rather than emitting `href="undefined"`.

### Removed
- **The legacy environment-variable name for the CBE key.** `config.py` read a
  second, hyphenated name left over from the first `.env` this project had, and
  it named that key in the source of a public repository. Only `CBE_API_KEY` is
  read now. **An `.env` still using the old name must rename that line**, or the
  API starts with no key and every register lookup fails — `GET /health` reports
  `cbe_api_key_configured: false` when that has happened.

## [0.6.0] — 2026-08-16

> **Corrected after tagging.** This entry originally recorded the NBB citation
> links as groundwork that "nothing calls yet, so they change no behaviour".
> That described commit `bf785ca` in isolation, not the tag: by `v0.6.0` the
> financials panel rendered a Source column and a Sources block, and
> `/api/company/{cbe}/financials` returned `source_urls` on every row. The
> behaviour did change, and the entry now says so.
>
> This is the mirror of the 0.4.0 problem recorded under 0.5.0. There a tag
> shipped more than its changelog mentioned; here a note asserted that a shipped
> feature was inert — worse, because it is the kind of line you would trust
> later while wondering why the links were not appearing.

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
- **Source links in the financials panel.** Every figure shown is derived, so
  the panel cites the filings it was derived from: a *Source* column linking
  each year to that year's accounts as published, and a *Sources* block listing
  every filing in all three formats the NBB serves — PDF (the document as
  published), XBRL, and CSV (the flattened rubriek codes this app parses) —
  each with its reference number, plus a link to the company's full filing list.
  The shareholder panel links its filing too. `source_urls` is returned by
  `/api/company/{cbe}/financials` and `/api/company/{cbe}/shareholders`.
- `enterprise_url()` and `deposit_urls()` in
  [`cbso_client.py`](api/app/cbso_client.py) build those links, with 4 tests in
  [`api/tests/test_cbso_client.py`](api/tests/test_cbso_client.py). The two NBB
  backends identify a filing differently — Consult by GUID, the official web
  services by reference number — and only the GUID resolves publicly, so a
  reference number yields **no link** rather than one that 404s. A dead citation
  is worse than none: it implies the source was checked.

### Fixed
- `.menu-note` was scoped as `.menu .menu-note`, so every note outside the
  right-click menu — the "figures are as filed" line, the shareholder panel's
  disclaimer — rendered at full body size instead of 11px muted. The type rules
  are now unscoped and only the menu's padding is scoped.

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
- Commit `bf785ca` is titled "groundwork, not yet wired up". That described the
  commit, not the tag — the wiring followed in the same release. Corrected in
  the note at the top of this entry; the message itself is already pushed and
  left alone.

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
