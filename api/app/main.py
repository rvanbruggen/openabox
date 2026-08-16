import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, graph, ingest
from .address import address_key, normalise_street
from .cbe_client import CBEClient, CBEError
from .version import __version__

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

client: CBEClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    await graph.init_schema()
    client = CBEClient()
    yield
    if client:
        await client.aclose()
    await graph.close_driver()


app = FastAPI(
    title="OpenABox",
    description="Local Belgian company graph, backed by the CBE register and Neo4j.",
    version=__version__,
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


class CypherRequest(BaseModel):
    query: str
    params: dict = Field(default_factory=dict)


class ExpandRequest(BaseModel):
    element_id: str
    limit: int = Field(default=50, ge=1, le=300)


def _client() -> CBEClient:
    if client is None:
        raise HTTPException(status_code=503, detail="Client not ready")
    return client


@app.get("/health")
async def health():
    counts = await graph.run_read(
        """
        MATCH (c:Company) WITH count(c) AS companies
        MATCH (a:Address) WITH companies, count(a) AS addresses
        RETURN companies, addresses
        """
    )
    return {
        "status": "ok",
        "version": __version__,
        "neo4j": counts[0] if counts else {},
        "cbe_api_key_configured": bool(config.cbe_api_key()),
        "rate_limit": _client().rate_limit,
    }


@app.get("/api/search")
async def search(
    name: str = Query(..., min_length=2),
    refresh: bool = Query(False, description="Bypass cache and re-query the API"),
):
    """Search by company name.

    Cache-first: the local full-text index is consulted before spending API
    quota. Note the upstream endpoint caps results at 10 with no pagination,
    so the cache will often be the richer source over time.
    """
    if not refresh:
        cached = await graph.run_read(
            """
            CALL db.index.fulltext.queryNodes('company_names', $term)
            YIELD node, score
            OPTIONAL MATCH (node)-[:REGISTERED_AT]->(a:Address)
            RETURN node AS company, a AS address, score
            ORDER BY score DESC LIMIT 25
            """,
            term=name,
        )
        if cached:
            return {"source": "cache", "count": len(cached), "results": cached}

    try:
        payloads = await _client().search_by_name(name)
    except CBEError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc))

    await ingest.ingest_many(payloads)
    return {
        "source": "cbeapi",
        "count": len(payloads),
        "results": payloads,
        "note": "Upstream search returns at most 10 results.",
    }


@app.get("/api/company/{cbe_number}")
async def get_company(cbe_number: str, refresh: bool = False):
    cbe_number = cbe_number.replace(".", "").replace(" ", "").removeprefix("BE")

    if not refresh:
        cached = await ingest.get_cached_company(cbe_number, config.CACHE_TTL_DAYS)
        if cached:
            return {"source": "cache", "company": cached}

    try:
        payload = await _client().get_company(cbe_number)
    except CBEError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc))

    await ingest.ingest_company(payload)
    return {"source": "cbeapi", "company": payload}


@app.get("/api/nace/{code}/companies")
async def companies_by_nace(
    code: str,
    nace_version: str = Query("2008", pattern=r"^(2003|2008|2025)$"),
    refresh: bool = False,
):
    """Companies carrying a NACE code, cache-first.

    Matches by prefix, mirroring the upstream endpoint: querying "62" also
    returns 620, 6201 and 62010. The version is part of the match because the
    same code describes different activities across versions.
    """
    if not refresh:
        cached = await graph.run_read(
            """
            MATCH (c:Company)-[:HAS_ACTIVITY]->(n:NaceCode)
            WHERE n.code STARTS WITH $code AND n.version = $version
            OPTIONAL MATCH (c)-[:REGISTERED_AT]->(a:Address)
            RETURN DISTINCT c AS company, a AS address,
                   n.code AS nace_code, n.description AS nace_description
            ORDER BY c.denomination LIMIT 200
            """,
            code=code,
            version=nace_version,
        )
        if cached:
            return {"source": "cache", "count": len(cached), "results": cached}

    try:
        payloads = await _client().companies_by_nace(code, nace_version)
    except CBEError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc))
    await ingest.ingest_many(payloads)
    return {"source": "cbeapi", "count": len(payloads), "results": payloads}


@app.get("/api/address/search")
async def search_address(
    street: str | None = None,
    house_number: str | None = None,
    city: str | None = None,
    post_code: str | None = None,
    refresh: bool = False,
):
    """Companies at an address, cache-first.

    The cached branch matches the *canonical* street against the stored
    address key, so a query for "Edingensestwg" finds records filed as
    "Edingensesteenweg" — the same folding that makes the Address nodes merge
    in the first place.
    """
    if not (street or house_number or city or post_code):
        raise HTTPException(status_code=400, detail="Provide at least one address part")

    # The postcode arrives as free text from the form; validate it here so a
    # stray character produces a readable message rather than a 422 body.
    post_code = (post_code or "").strip() or None
    if post_code and not post_code.isdigit():
        raise HTTPException(status_code=400, detail="Postcode must be numeric")

    if not refresh:
        cached = await graph.run_read(
            """
            MATCH (a:Address)
            WHERE ($street_norm IS NULL OR a.key CONTAINS $street_norm)
              AND ($house_number IS NULL OR a.street_number = $house_number)
            OPTIONAL MATCH (a)-[:IN_CITY]->(ct:City)
            WITH a, ct
            // Match against every spelling seen for the locality: the register
            // files one post code under several names.
            WHERE ($post_code IS NULL OR ct.post_code = $post_code)
              AND ($city IS NULL OR any(n IN coalesce(ct.aliases, [])
                                        WHERE toLower(n) CONTAINS toLower($city)))
            MATCH (a)<-[:REGISTERED_AT|LOCATED_AT]-(x)
            OPTIONAL MATCH (x)<-[:HAS_ESTABLISHMENT]-(owner:Company)
            WITH a, ct, coalesce(owner, x) AS c
            WHERE c:Company
            RETURN DISTINCT c AS company, a AS address, ct AS city
            ORDER BY c.denomination LIMIT 200
            """,
            street_norm=normalise_street(street) or None,
            house_number=house_number,
            city=city,
            post_code=post_code,
        )
        if cached:
            return {"source": "cache", "count": len(cached), "results": cached}

    try:
        payloads = await _client().search_by_address(
            street=street,
            house_number=house_number,
            city=city,
            post_code=int(post_code) if post_code else None,
        )
    except CBEError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc))
    await ingest.ingest_many(payloads)
    return {"source": "cbeapi", "count": len(payloads), "results": payloads}


@app.get("/api/company/{cbe_number}/connections")
async def connections(cbe_number: str):
    """Companies connected to this one, answered entirely from the graph.

    Address overlap works today. The shareholder and officer branches are
    written against edge types that the NBB and Staatsblad ingestion will
    populate later — they simply return nothing until then.
    """
    cbe_number = cbe_number.replace(".", "").replace(" ", "").removeprefix("BE")
    return {
        "shared_address": await graph.run_read(
            """
            MATCH (c:Company {cbe_number: $cbe})-[:REGISTERED_AT]->(a:Address)
                  <-[:REGISTERED_AT]-(other:Company)
            WHERE other <> c
            RETURN other.cbe_number AS cbe_number,
                   other.denomination AS denomination,
                   a.full_address AS address
            ORDER BY denomination LIMIT 100
            """,
            cbe=cbe_number,
        ),
        "shared_shareholder": await graph.run_read(
            """
            MATCH (c:Company {cbe_number: $cbe})<-[h1:HOLDS_PARTICIPATION]-(holder)
                  -[h2:HOLDS_PARTICIPATION]->(other:Company)
            WHERE other <> c
            RETURN other.cbe_number AS cbe_number,
                   other.denomination AS denomination,
                   coalesce(holder.denomination, holder.name) AS via,
                   h1.pct AS pct_here, h2.pct AS pct_there,
                   h1._source AS source
            LIMIT 100
            """,
            cbe=cbe_number,
        ),
        "shared_officer": await graph.run_read(
            """
            MATCH (c:Company {cbe_number: $cbe})<-[:OFFICER_OF]-(p:Person)
                  -[:OFFICER_OF]->(other:Company)
            WHERE other <> c
            RETURN other.cbe_number AS cbe_number,
                   other.denomination AS denomination,
                   p.name AS via
            LIMIT 100
            """,
            cbe=cbe_number,
        ),
    }


@app.get("/api/address/companies")
async def companies_at_address(key: str = Query(..., description="Address.key")):
    """Every company at one address, answered from the graph.

    Registered offices and branches are returned separately: a company whose
    registered office is here is a different claim from one that merely runs
    an establishment here, and collapsing them would hide that.

    The key is taken as a query parameter rather than a path segment because
    it contains `|` separators.
    """
    rows = await graph.run_read(
        """
        MATCH (a:Address {key: $key})
        OPTIONAL MATCH (a)-[:IN_CITY]->(ct:City)
        CALL {
            WITH a
            MATCH (a)<-[:REGISTERED_AT]-(c:Company)
            RETURN collect(DISTINCT {
                cbe_number: c.cbe_number,
                denomination: c.denomination,
                status: c.status
            }) AS registered
        }
        CALL {
            WITH a
            MATCH (a)<-[:LOCATED_AT]-(e:Establishment)<-[:HAS_ESTABLISHMENT]-(c:Company)
            RETURN collect(DISTINCT {
                cbe_number: c.cbe_number,
                denomination: c.denomination,
                establishment_number: e.establishment_number
            }) AS establishments
        }
        RETURN a.key AS key,
               a.full_address AS full_address,
               ct.post_code AS post_code,
               ct.name AS city,
               registered,
               establishments
        """,
        key=key,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Address not in the graph")
    return rows[0]


@app.get("/api/city/companies")
async def companies_in_city(
    key: str = Query(..., description="City.key"),
    limit: int = Query(200, ge=1, le=1000),
):
    """Companies with a registered office in one city (postal area)."""
    rows = await graph.run_read(
        """
        MATCH (ct:City {key: $key})<-[:IN_CITY]-(a:Address)<-[:REGISTERED_AT]-(c:Company)
        RETURN ct.post_code AS post_code, ct.name AS city, ct.aliases AS aliases,
               collect(DISTINCT {
                   cbe_number: c.cbe_number,
                   denomination: c.denomination,
                   address: a.full_address
               })[0..$limit] AS companies
        """,
        key=key,
        limit=limit,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="City not in the graph")
    return rows[0]


@app.post("/api/cypher")
async def run_cypher(request: CypherRequest):
    """Run an arbitrary Cypher query in a read transaction.

    Read-only is enforced by the server via the transaction access mode, so a
    stray write clause fails at the database rather than relying on us to
    pattern-match dangerous keywords.

    Returns both a tabular projection and a graph projection, so the console
    can render whichever suits what the query returned.
    """
    try:
        records = await graph.run_read(request.query, **request.params)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"records": records, "graph": graph.collect_graph(records)}


@app.get("/api/graph/company/{cbe_number}")
async def graph_for_company(cbe_number: str):
    """The immediate neighbourhood of a company, ready for the canvas."""
    cbe_number = cbe_number.replace(".", "").replace(" ", "").removeprefix("BE")
    rows = await graph.run_read(
        """
        MATCH (c:Company {cbe_number: $cbe})
        OPTIONAL MATCH p1 = (c)-[:REGISTERED_AT]->(:Address)
        OPTIONAL MATCH p2 = (c)-[:HAS_ACTIVITY]->(:NaceCode)
        OPTIONAL MATCH p3 = (c)-[:HAS_FORM]->(:JuridicalForm)
        // Co-located companies are the point of the graph, so pull them in
        // directly rather than making the user expand to find them.
        OPTIONAL MATCH p4 = (c)-[:REGISTERED_AT]->(:Address)<-[:REGISTERED_AT]-(:Company)
        OPTIONAL MATCH p5 = (c)-[:REGISTERED_AT]->(:Address)-[:IN_CITY]->(:City)
        RETURN c, p1, p2, p3, p4, p5
        """,
        cbe=cbe_number,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Company not in the graph yet")
    return graph.collect_graph(rows)


@app.post("/api/graph/expand")
async def expand_node(request: ExpandRequest):
    """Expand one node's neighbours, capped so a hub cannot flood the canvas."""
    rows = await graph.run_read(
        """
        MATCH (n) WHERE elementId(n) = $element_id
        MATCH p = (n)-[r]-(m)
        RETURN p LIMIT $limit
        """,
        element_id=request.element_id,
        limit=request.limit,
    )
    return graph.collect_graph(rows)


@app.get("/api/address-key")
async def preview_address_key(
    street: str, street_number: str = "", post_code: str = "", city: str = ""
):
    """Debug helper for tuning address canonicalisation."""
    return {
        "key": address_key(
            {
                "street": street,
                "street_number": street_number,
                "post_code": post_code,
                "city": city,
            }
        )
    }
