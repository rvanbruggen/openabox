# Changelog

All notable changes to OpenABox. Versions follow [semantic versioning](https://semver.org).
While the major version is 0, the interface may change between minor versions.

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
