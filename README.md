# OpenABox

A personal, local-only Belgian company lookup and graph exploration tool. Data
comes from the CBE/KBO register via [cbeapi.be](https://cbeapi.be), is cached in
a local Neo4j instance, and is explored from there — the API is not re-queried
for records already held.

## Status

| Component | State |
|---|---|
| Docker Compose stack (Neo4j + API) | Written, **not yet run** |
| CBE API client | Written; auth + response shape validated against the live API |
| Address canonicalisation | Written and unit-tested (8 tests passing) |
| Graph schema + ingestion | Written, **not yet run against Neo4j** |
| REST + Cypher endpoints | Written, **not yet run** |
| Web UI (graph canvas + Cypher console) | Not started |
| Shareholder / officer ingestion (NBB, Staatsblad) | Not started — edge types reserved |

## Running it

On the Docker host (192.168.68.78):

```bash
cp .env.example .env    # then fill in CBE_API_KEY
docker compose up -d
```

- API: `http://192.168.68.78:8000/docs`
- Neo4j Browser: `http://192.168.68.78:7474`

Run the address tests with:

```bash
python3 api/tests/test_address.py
```

## Graph model

```
(Company)-[:REGISTERED_AT]->(Address)
(Company)-[:HAS_ESTABLISHMENT]->(Establishment)-[:LOCATED_AT]->(Address)
(Company)-[:HAS_ACTIVITY {classification, nace_version}]->(NaceCode)
(Company)-[:HAS_FORM]->(JuridicalForm)
(Company)-[:HAS_SITUATION]->(JuridicalSituation)

# reserved for later ingestion, queries already written against them:
(Company)-[:HOLDS_PARTICIPATION {pct, as_of, _source}]->(Company)   # NBB
(Person)-[:OFFICER_OF {role, from, to, _source}]->(Company)          # Staatsblad
(Person)-[:FOUNDED {shares, as_of, _source}]->(Company)              # Staatsblad
```

**Addresses are nodes, not properties.** That is what makes "which companies
share this building?" a graph traversal rather than a string comparison. The
key is building-level — box/unit numbers live on the establishment — so
companies in different units of the same building still meet on one node.

## Data sources and their limits

The CBE API provides identification, addresses, establishments, NACE codes,
legal form and status. It provides **no shareholder, director or financial
data**. Ownership therefore has to come from elsewhere, and no single source
covers every company:

| Source | Gives | Coverage | Freshness |
|---|---|---|---|
| Staatsblad incorporation deeds | Founding shareholders + shares | Near-universal | Stale — transfers aren't published |
| Staatsblad appointments | Directors / managers | Near-universal | Current |
| NBB full-format annual accounts | Participations + % | Minority of filers | Current |

These are modelled as **distinct edge types carrying `_source` and `_as_of`**
rather than one generic `SHAREHOLDER` relationship, so a query can tell
"founded this in 2009" apart from "currently holds 62%".

The UBO register is not an option: public access was withdrawn after the 2022
CJEU ruling and now requires demonstrated legitimate interest.

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
