"""Neo4j connection, schema, and result serialisation."""

import logging
from datetime import date, datetime

import neo4j
from neo4j import AsyncGraphDatabase
from neo4j.time import Date, DateTime
from neo4j.graph import Node, Path, Relationship

from . import config

logger = logging.getLogger(__name__)

_driver: neo4j.AsyncDriver | None = None

# Constraints double as the uniqueness guarantee that makes MERGE-based
# ingestion idempotent, so re-ingesting a company never duplicates nodes.
SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT company_cbe IF NOT EXISTS "
    "FOR (c:Company) REQUIRE c.cbe_number IS UNIQUE",
    "CREATE CONSTRAINT establishment_number IF NOT EXISTS "
    "FOR (e:Establishment) REQUIRE e.establishment_number IS UNIQUE",
    "CREATE CONSTRAINT address_key IF NOT EXISTS "
    "FOR (a:Address) REQUIRE a.key IS UNIQUE",
    "CREATE CONSTRAINT city_key IF NOT EXISTS "
    "FOR (c:City) REQUIRE c.key IS UNIQUE",
    "CREATE CONSTRAINT nace_key IF NOT EXISTS "
    "FOR (n:NaceCode) REQUIRE n.key IS UNIQUE",
    "CREATE CONSTRAINT form_code IF NOT EXISTS "
    "FOR (f:JuridicalForm) REQUIRE f.code IS UNIQUE",
    "CREATE CONSTRAINT situation_code IF NOT EXISTS "
    "FOR (s:JuridicalSituation) REQUIRE s.code IS UNIQUE",
    # Person nodes come from NBB filings: natural-person shareholders and
    # directors. The key is constructed (see identity.py) because filings carry
    # no personal identifier.
    "CREATE CONSTRAINT person_key IF NOT EXISTS "
    "FOR (p:Person) REQUIRE p.key IS UNIQUE",
    # One node per annual-accounts filing, so every ownership edge can point
    # back at the document that claimed it.
    "CREATE CONSTRAINT deposit_id IF NOT EXISTS "
    "FOR (d:Deposit) REQUIRE d.id IS UNIQUE",
    # Foreign and unidentified parties cannot live in the CBE namespace, so they
    # get their own key. They carry the Company label too, which is what keeps
    # existing traversals working across the boundary.
    "CREATE CONSTRAINT external_entity_key IF NOT EXISTS "
    "FOR (e:ExternalEntity) REQUIRE e.key IS UNIQUE",
    "CREATE INDEX company_hydrated IF NOT EXISTS "
    "FOR (c:Company) ON (c._hydrated)",
    # Post code and city moved from Address to City; drop the index that
    # pointed at the property Address no longer carries.
    "DROP INDEX address_post_code IF EXISTS",
    "CREATE INDEX city_post_code IF NOT EXISTS FOR (c:City) ON (c.post_code)",
    "CREATE INDEX city_name IF NOT EXISTS FOR (c:City) ON (c.name)",
    "CREATE INDEX deposit_period_end IF NOT EXISTS "
    "FOR (d:Deposit) ON (d.period_end)",
    "CREATE INDEX person_last_name IF NOT EXISTS "
    "FOR (p:Person) ON (p.last_name)",
    # Lets name search be answered from cache instead of spending API quota.
    "CREATE FULLTEXT INDEX company_names IF NOT EXISTS "
    "FOR (c:Company) ON EACH [c.denomination, c.commercial_name, c.abbreviation]",
    # People come only from NBB filings, so they exist in the graph or nowhere —
    # there is no upstream endpoint to fall back to. Without this index a
    # shareholder or director could be looked at but never looked up.
    "CREATE FULLTEXT INDEX person_names IF NOT EXISTS "
    "FOR (p:Person) ON EACH [p.name, p.first_name, p.last_name]",
]


async def get_driver() -> neo4j.AsyncDriver:
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(
            config.NEO4J_URI,
            auth=(config.NEO4J_USER, config.NEO4J_PASSWORD),
        )
    return _driver


async def close_driver() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


async def init_schema() -> None:
    driver = await get_driver()
    async with driver.session() as session:
        for statement in SCHEMA_STATEMENTS:
            await session.run(statement)
    logger.info("Applied %d schema statements", len(SCHEMA_STATEMENTS))


async def run_write(query: str, **params):
    driver = await get_driver()
    async with driver.session() as session:
        result = await session.run(query, **params)
        return [record.data() async for record in result]


async def run_read(query: str, **params) -> list[dict]:
    """Run a query in a read transaction.

    Read transactions are enforced by the server, so this is what keeps the
    Cypher console from being able to delete the cache — no regex blocklist
    needed.
    """
    driver = await get_driver()

    async def _work(tx):
        result = await tx.run(query, **params)
        return [record async for record in result]

    async with driver.session(default_access_mode=neo4j.READ_ACCESS) as session:
        records = await session.execute_read(_work)
    return [to_jsonable(dict(record)) for record in records]


def collect_graph(rows) -> dict:
    """Extract a deduplicated {nodes, relationships} projection from results.

    Any query that returns nodes or relationships can be drawn on the canvas,
    including ad-hoc ones typed into the Cypher console — the projection is
    built by walking whatever came back rather than requiring a fixed shape.
    Relationships whose endpoints were not also returned are dropped, since
    there is nothing to attach them to.
    """
    nodes: dict[str, dict] = {}
    rels: dict[str, dict] = {}

    def walk(value):
        if isinstance(value, dict):
            kind = value.get("_type")
            if kind == "node":
                nodes[value["_id"]] = value
            elif kind == "relationship":
                rels[value["_id"]] = value
            elif kind == "path":
                for item in value.get("nodes", []):
                    walk(item)
                for item in value.get("relationships", []):
                    walk(item)
            else:
                for item in value.values():
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(rows)
    connected = [r for r in rels.values() if r["_start"] in nodes and r["_end"] in nodes]
    return {"nodes": list(nodes.values()), "relationships": connected}


def to_jsonable(value):
    """Convert Neo4j types into something FastAPI can serialise.

    Nodes and relationships keep their identity and labels so the frontend can
    render them on a graph canvas without a second lookup.
    """
    if isinstance(value, Node):
        return {
            "_type": "node",
            "_id": value.element_id,
            "_labels": sorted(value.labels),
            **{k: to_jsonable(v) for k, v in value.items()},
        }
    if isinstance(value, Relationship):
        return {
            "_type": "relationship",
            "_id": value.element_id,
            "_rel_type": value.type,
            "_start": value.start_node.element_id if value.start_node else None,
            "_end": value.end_node.element_id if value.end_node else None,
            **{k: to_jsonable(v) for k, v in value.items()},
        }
    if isinstance(value, Path):
        return {
            "_type": "path",
            "nodes": [to_jsonable(n) for n in value.nodes],
            "relationships": [to_jsonable(r) for r in value.relationships],
        }
    if isinstance(value, (DateTime, Date)):
        return value.iso_format()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value
