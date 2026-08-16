# OpenABox

A personal, local-only Belgian company lookup and graph exploration tool. Data
comes from the CBE/KBO register via [cbeapi.be](https://cbeapi.be), is cached in
a local Neo4j instance, and is explored from there — the API is not re-queried
for records already held.

## Version

**0.2.1** — see [CHANGELOG.md](CHANGELOG.md) for what is in it.

The version is defined once, in [`api/app/version.py`](api/app/version.py), and
everything else reads it from there:

| Surface | How it gets the version |
|---|---|
| `GET /health` | `version` field |
| `GET /docs` (OpenAPI) | FastAPI `version` |
| Web UI header | fetched from `/health`, never hardcoded |
| Git | annotated tag `v0.2.1` |

Nothing duplicates the string, so the UI cannot drift from the backend that is
actually running — if the header says `v0.2.1`, that is the code answering.

## Status

| Component | State |
|---|---|
| Docker Compose stack (Neo4j + API) | Running |
| CBE API client | Verified against the live API |
| Address + city canonicalisation | 17 unit tests passing; verified on live register data |
| Graph schema + ingestion | Verified — provenance set, shared-address merging confirmed |
| REST + Cypher endpoints | Verified, including cache-first behaviour |
| Web UI (graph canvas + Cypher console) | Built; **City nodes and the new palette not yet verified in a browser** |
| Shareholder / officer ingestion (NBB, Staatsblad) | Not started — edge types reserved |

Verified on a live ingestion of 10 companies: 336 establishments resolved to
328 addresses, and the five Colruyt entities registered at Edingensesteenweg
196 correctly merged onto a single `Address` node.

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
(Company)-[:REGISTERED_AT]->(Address)-[:IN_CITY]->(City)
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
