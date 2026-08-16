"""Map CBE API payloads into the graph.

Provenance is the point here. Every node written carries `_source` and
`_fetched_at`, and Company nodes additionally carry `_hydrated` to separate
"we know this company exists" from "we have its full record". Without that
distinction there is no way to tell a cache miss from a company that genuinely
has no establishments, and the app ends up re-spending API quota on records it
already holds.
"""

import logging

import neo4j

from . import graph
from .address import address_key, address_properties

logger = logging.getLogger(__name__)

SOURCE = "cbeapi"

_COMPANY_FIELDS = (
    "cbe_number_formatted",
    "denomination",
    "abbreviation",
    "commercial_name",
    "branch_name",
    "denomination_with_legal_form",
    "status",
    "type",
    "pretty_type",
    "start_date",
)


def _establishment_as_address(est: dict) -> dict:
    """Establishments carry address fields flat, under slightly different names."""
    return {
        "street": est.get("street"),
        "street_number": est.get("house_number"),
        "post_code": est.get("post_code"),
        "city": est.get("city"),
        "country_code": est.get("country_code"),
        "full_address": est.get("full_address"),
    }


def _nace_description(value) -> str | None:
    """The spec types description as an array but the API sends a string."""
    if isinstance(value, list):
        return "; ".join(str(v) for v in value if v) or None
    return value or None


def _nace_version(value) -> str | None:
    """Normalise the NACE version to a bare year.

    Company payloads report "Nace2008"/"Nace2025", but /v1/nace/{code}/companies
    requires nace_version in {2003, 2008, 2025}. Storing the payload form
    verbatim would mean a code looked up by year never matches the same code
    stored from a company record.
    """
    if not value:
        return None
    return str(value).replace("Nace", "").strip() or None


async def ingest_company(payload: dict) -> str | None:
    """Write one CompanyResource into the graph. Returns the CBE number."""
    cbe_number = payload.get("cbe_number")
    if not cbe_number:
        logger.warning("Skipping payload with no cbe_number")
        return None

    props = {f: payload.get(f) for f in _COMPANY_FIELDS}
    contact = payload.get("contact_infos") or {}
    props |= {
        "email": contact.get("email"),
        "phone": contact.get("phone"),
        "web": contact.get("web"),
    }

    address = payload.get("address") or {}
    addr_key = address_key(address)
    addr_props = address_properties(address) if addr_key else None

    nace = [
        {
            "key": f"{_nace_version(a.get('nace_version'))}:{a.get('code')}",
            "code": a.get("code"),
            "version": _nace_version(a.get("nace_version")),
            "description": _nace_description(a.get("description")),
            "classification": a.get("classification"),
        }
        for a in (payload.get("nace_activities") or [])
        if a.get("code")
    ]

    establishments = []
    for est in payload.get("establishments") or []:
        if not est.get("establishment_number"):
            continue
        est_addr = _establishment_as_address(est)
        establishments.append(
            {
                "establishment_number": est.get("establishment_number"),
                "start_date": est.get("start_date"),
                "box": est.get("box"),
                "extra_info": est.get("extra_info"),
                "type_of_address": est.get("type_of_address"),
                "date_striking_off": est.get("date_striking_off"),
                "addr_key": address_key(est_addr),
                "addr_props": address_properties(est_addr),
            }
        )

    params = {
        "cbe_number": cbe_number,
        "props": {k: v for k, v in props.items() if v is not None},
        "addr_key": addr_key,
        "addr_props": addr_props,
        "form_code": payload.get("juridical_form_code"),
        "form_label": payload.get("juridical_form"),
        "form_short": payload.get("juridical_form_short"),
        "situation_code": payload.get("juridical_situation_code"),
        "situation_label": payload.get("juridical_situation"),
        "nace": nace,
        "establishments": establishments,
        "source": SOURCE,
    }

    driver = await graph.get_driver()
    async with driver.session() as session:
        await session.execute_write(_write_company, params)
    return cbe_number


async def _write_company(tx, p: dict) -> None:
    await tx.run(
        """
        MERGE (c:Company {cbe_number: $cbe_number})
        SET c += $props,
            c._source = $source,
            c._fetched_at = datetime(),
            c._hydrated = true
        """,
        **p,
    )

    if p["addr_key"]:
        await tx.run(
            """
            MATCH (c:Company {cbe_number: $cbe_number})
            MERGE (a:Address {key: $addr_key})
            SET a += $addr_props, a._source = $source
            MERGE (c)-[r:REGISTERED_AT]->(a)
            SET r._fetched_at = datetime()
            """,
            **p,
        )

    if p["form_code"]:
        await tx.run(
            """
            MATCH (c:Company {cbe_number: $cbe_number})
            MERGE (f:JuridicalForm {code: $form_code})
            SET f.label = coalesce($form_label, f.label),
                f.short_label = coalesce($form_short, f.short_label)
            MERGE (c)-[:HAS_FORM]->(f)
            """,
            **p,
        )

    if p["situation_code"]:
        await tx.run(
            """
            MATCH (c:Company {cbe_number: $cbe_number})
            MERGE (s:JuridicalSituation {code: $situation_code})
            SET s.label = coalesce($situation_label, s.label)
            MERGE (c)-[:HAS_SITUATION]->(s)
            """,
            **p,
        )

    if p["nace"]:
        await tx.run(
            """
            MATCH (c:Company {cbe_number: $cbe_number})
            UNWIND $nace AS n
            MERGE (code:NaceCode {key: n.key})
            SET code.code = n.code,
                code.version = n.version,
                code.description = coalesce(n.description, code.description)
            MERGE (c)-[r:HAS_ACTIVITY]->(code)
            SET r.classification = n.classification,
                r.nace_version = n.version
            """,
            **p,
        )

    if p["establishments"]:
        await tx.run(
            """
            MATCH (c:Company {cbe_number: $cbe_number})
            UNWIND $establishments AS e
            MERGE (est:Establishment {establishment_number: e.establishment_number})
            SET est.start_date = e.start_date,
                est.box = e.box,
                est.extra_info = e.extra_info,
                est.type_of_address = e.type_of_address,
                est.date_striking_off = e.date_striking_off,
                est._source = $source,
                est._fetched_at = datetime()
            MERGE (c)-[:HAS_ESTABLISHMENT]->(est)
            WITH est, e
            WHERE e.addr_key IS NOT NULL
            MERGE (a:Address {key: e.addr_key})
            SET a += e.addr_props, a._source = $source
            MERGE (est)-[:LOCATED_AT]->(a)
            """,
            **p,
        )


async def ingest_many(payloads: list[dict]) -> list[str]:
    ingested = []
    for payload in payloads:
        cbe = await ingest_company(payload)
        if cbe:
            ingested.append(cbe)
    return ingested


async def get_cached_company(cbe_number: str, ttl_days: int) -> dict | None:
    """Return a hydrated Company from the graph if it is still within TTL."""
    rows = await graph.run_read(
        """
        MATCH (c:Company {cbe_number: $cbe_number})
        WHERE c._hydrated = true
          AND c._fetched_at > datetime() - duration({days: $ttl_days})
        OPTIONAL MATCH (c)-[:REGISTERED_AT]->(a:Address)
        OPTIONAL MATCH (c)-[r:HAS_ACTIVITY]->(n:NaceCode)
        OPTIONAL MATCH (c)-[:HAS_ESTABLISHMENT]->(e:Establishment)
        RETURN c AS company,
               a AS address,
               collect(DISTINCT {code: n.code, version: n.version,
                                 description: n.description,
                                 classification: r.classification}) AS nace,
               collect(DISTINCT e) AS establishments
        """,
        cbe_number=cbe_number,
        ttl_days=ttl_days,
    )
    return rows[0] if rows else None
