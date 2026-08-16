# Changelog

All notable changes to OpenABox. Versions follow [semantic versioning](https://semver.org).
While the major version is 0, the interface may change between minor versions.

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
