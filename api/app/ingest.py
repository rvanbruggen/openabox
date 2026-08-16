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
from .address import address_key, address_properties, city_key, city_properties
from .identity import external_key, person_key

logger = logging.getLogger(__name__)

SOURCE = "cbeapi"
CBSO_SOURCE = "nbb-cbso"

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
    ct_key = city_key(address)
    ct_props = city_properties(address) if ct_key else None

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
                "city_key": city_key(est_addr),
                "city_props": city_properties(est_addr),
            }
        )

    params = {
        "cbe_number": cbe_number,
        "props": {k: v for k, v in props.items() if v is not None},
        "addr_key": addr_key,
        "addr_props": addr_props,
        "city_key": ct_key,
        "city_props": ct_props,
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
            WITH a
            WHERE $city_key IS NOT NULL
            MERGE (ct:City {key: $city_key})
            SET ct.post_code = coalesce(ct.post_code, $city_props.post_code),
                ct.country_code = coalesce(ct.country_code, $city_props.country_code),
                ct.name = coalesce(ct.name, $city_props.name),
                ct.aliases = CASE
                    WHEN $city_props.name IS NULL THEN coalesce(ct.aliases, [])
                    WHEN $city_props.name IN coalesce(ct.aliases, []) THEN ct.aliases
                    ELSE coalesce(ct.aliases, []) + $city_props.name
                END,
                ct._source = $source
            MERGE (a)-[:IN_CITY]->(ct)
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
            WITH a, e
            WHERE e.city_key IS NOT NULL
            MERGE (ct:City {key: e.city_key})
            SET ct.post_code = coalesce(ct.post_code, e.city_props.post_code),
                ct.country_code = coalesce(ct.country_code, e.city_props.country_code),
                ct.name = coalesce(ct.name, e.city_props.name),
                ct.aliases = CASE
                    WHEN e.city_props.name IS NULL THEN coalesce(ct.aliases, [])
                    WHEN e.city_props.name IN coalesce(ct.aliases, []) THEN ct.aliases
                    ELSE coalesce(ct.aliases, []) + e.city_props.name
                END,
                ct._source = $source
            MERGE (a)-[:IN_CITY]->(ct)
            """,
            **p,
        )


# --------------------------------------------------------------------------
# NBB annual accounts: shareholders, directors, participations
# --------------------------------------------------------------------------
#
# The CBE register has none of this, so everything below comes from a filing at
# the Central Balance Sheet Office and is written *alongside* the CBE-sourced
# graph rather than over it. Three rules keep the two sources from corrupting
# each other:
#
# 1. A party identified by CBE number becomes a `Company` node — the same node
#    the CBE ingestion uses — but is only ever created `_hydrated = false`.
#    That marks it as "we know this company exists because someone else's
#    filing named it", which is exactly the state a later lookup resolves. It
#    is how the graph grows past the companies explicitly searched for.
# 2. Company *addresses* from a filing are deliberately not written. The CBE
#    register is authoritative for where a company is registered, and a filing
#    can be a year stale; adding a second REGISTERED_AT edge would invent
#    shared-address matches that are not real. Person and foreign-entity
#    addresses are written, because nothing else supplies them.
# 3. Every edge is keyed by `as_of` (the filing's period end). Ownership
#    changes, so merging without the date in the key would overwrite 2019's
#    shareholders with 2025's and destroy the history that makes the graph
#    worth having.

# Which relationship each extracted role becomes, and which way it points.
# "in" means the party points at the filer (a shareholder owns the filer);
# "out" means the filer points at the party (the filer holds a participation).
_ROLE_EDGES = {
    "SHAREHOLDER": ("SHAREHOLDER_OF", "in"),
    "DIRECTOR": ("DIRECTOR_OF", "in"),
    "PARTICIPATION": ("HOLDS_PARTICIPATION", "out"),
    "PARENT": ("CONSOLIDATED_BY", "out"),
    "AUDITOR": ("AUDITED_BY", "out"),
    "ACCOUNTANT": ("AUDITED_BY", "out"),
}

_EDGE_PROPS = (
    "pct",
    "pct_via_subsidiaries",
    "shares",
    "share_nature",
    "mandate",
    "role_label",
    "represented_by",
    "equity",
    "result",
)


def _edge_properties(party: dict, deposit: dict) -> dict:
    props = {k: party.get(k) for k in _EDGE_PROPS}
    props |= {
        "as_of": _as_of(deposit),
        "role": party.get("role"),
        "_source": CBSO_SOURCE,
        "_deposit": deposit.get("id"),
    }
    return {k: v for k, v in props.items() if v is not None}


def _as_of(deposit: dict) -> str:
    """The date an edge speaks for — and the key edges are merged on.

    This must never be null: MERGE on a null property does not match the way
    equality suggests, so a missing period end would quietly create a duplicate
    edge on every ingest. The deposit id is a last-resort fallback that at least
    keeps one edge per filing.
    """
    return (
        deposit.get("period_end")
        or deposit.get("deposit_date")
        or str(deposit.get("id"))
    )


def _plan_parties(cbe_number: str, deposit: dict, parties: list[dict]) -> dict:
    """Sort extracted parties into the write batches the graph needs.

    Splitting by (relationship, target kind) here keeps every Cypher statement
    below simple and unconditional, which matters more than the extra passes:
    conditional MERGE on an unknown node label is where this kind of ingestion
    usually goes wrong.
    """
    batches: dict[tuple[str, str, str], list[dict]] = {}
    skipped: list[dict] = []

    for party in parties:
        edge = _ROLE_EDGES.get(party["role"])
        if not edge:
            skipped.append(party)
            continue
        rel_type, direction = edge
        props = _edge_properties(party, deposit)
        address = party.get("address") or {}

        if party["kind"] == "person":
            keyed = person_key(
                party.get("last_name"), party.get("first_name"), address, cbe_number
            )
            if not keyed:
                skipped.append(party)
                continue
            key, basis = keyed
            row = {
                "key": key,
                "key_basis": basis,
                "name": party["name"],
                "first_name": party.get("first_name"),
                "last_name": party.get("last_name"),
                "props": props,
                "addr_key": address_key(address),
                "addr_props": address_properties(address) if address else None,
                "city_key": city_key(address),
                "city_props": city_properties(address) if address else None,
            }
            batches.setdefault((rel_type, direction, "person"), []).append(row)
            continue

        if party.get("cbe_number"):
            row = {
                "cbe_number": party["cbe_number"],
                "name": party["name"],
                "props": props,
            }
            batches.setdefault((rel_type, direction, "company"), []).append(row)
            continue

        key = external_key(
            party.get("identifier"), address.get("country_code"), party["name"]
        )
        if not key:
            skipped.append(party)
            continue
        row = {
            "key": key,
            "name": party["name"],
            "identifier": party.get("identifier"),
            "country_code": address.get("country_code"),
            "props": props,
            "addr_key": address_key(address),
            "addr_props": address_properties(address) if address else None,
            "city_key": city_key(address),
            "city_props": city_properties(address) if address else None,
        }
        batches.setdefault((rel_type, direction, "external"), []).append(row)

    return {"batches": batches, "skipped": skipped}


def _arrow(direction: str, rel_type: str) -> str:
    """Render the relationship pattern for the given direction.

    `as_of` is part of the MERGE pattern, not set afterwards, so re-ingesting a
    filing updates that period's edge while a filing for a *different* period
    adds one. That is what preserves ownership history instead of overwriting
    it with whatever was fetched most recently.
    """
    rel = f"[r:{rel_type} {{as_of: row.props.as_of}}]"
    if direction == "in":
        return f"(other)-{rel}->(c)"
    return f"(c)-{rel}->(other)"


async def ingest_deposit(
    cbe_number: str, deposit: dict, parties: list[dict]
) -> dict:
    """Write one filing's parties into the graph. Returns what was written."""
    plan = _plan_parties(cbe_number, deposit, parties)
    params = {
        "cbe_number": cbe_number,
        "deposit": {k: v for k, v in deposit.items() if v is not None},
        "source": CBSO_SOURCE,
    }

    driver = await graph.get_driver()
    async with driver.session() as session:
        await session.execute_write(_write_deposit, params, plan["batches"])

    written = {
        f"{rel}:{kind}": len(rows) for (rel, _, kind), rows in plan["batches"].items()
    }
    if plan["skipped"]:
        logger.info(
            "Deposit %s: skipped %d unkeyable parties",
            deposit.get("id"),
            len(plan["skipped"]),
        )
    return {"written": written, "skipped": len(plan["skipped"])}


async def _write_deposit(tx, p: dict, batches: dict) -> None:
    await tx.run(
        """
        MERGE (c:Company {cbe_number: $cbe_number})
        ON CREATE SET c._hydrated = false, c._source = $source
        SET c._cbso_fetched_at = datetime()
        MERGE (d:Deposit {id: $deposit.id})
        SET d += $deposit, d._source = $source, d._fetched_at = datetime()
        MERGE (c)-[:FILED]->(d)
        """,
        **p,
    )

    for (rel_type, direction, kind), rows in batches.items():
        pattern = _arrow(direction, rel_type)
        params = dict(p, rows=rows)

        if kind == "company":
            # `ON CREATE` only: a company already hydrated from the CBE register
            # must keep its authoritative name and stay hydrated.
            await tx.run(
                f"""
                MATCH (c:Company {{cbe_number: $cbe_number}})
                UNWIND $rows AS row
                MERGE (other:Company {{cbe_number: row.cbe_number}})
                ON CREATE SET other._hydrated = false,
                              other.denomination = row.name,
                              other._source = $source,
                              other._fetched_at = datetime()
                ON MATCH SET other.denomination = coalesce(other.denomination, row.name)
                MERGE {pattern}
                SET r += row.props
                """,
                **params,
            )
        elif kind == "person":
            await tx.run(
                f"""
                MATCH (c:Company {{cbe_number: $cbe_number}})
                UNWIND $rows AS row
                MERGE (other:Person {{key: row.key}})
                ON CREATE SET other.name = row.name,
                              other.first_name = row.first_name,
                              other.last_name = row.last_name,
                              other._key_basis = row.key_basis,
                              other._source = $source,
                              other._fetched_at = datetime()
                MERGE {pattern}
                SET r += row.props
                WITH other, row
                WHERE row.addr_key IS NOT NULL
                MERGE (a:Address {{key: row.addr_key}})
                SET a += row.addr_props, a._source = coalesce(a._source, $source)
                MERGE (other)-[:RESIDES_AT]->(a)
                WITH a, row
                WHERE row.city_key IS NOT NULL
                MERGE (ct:City {{key: row.city_key}})
                SET ct.post_code = coalesce(ct.post_code, row.city_props.post_code),
                    ct.country_code = coalesce(ct.country_code,
                                               row.city_props.country_code),
                    ct.name = coalesce(ct.name, row.city_props.name),
                    ct._source = coalesce(ct._source, $source)
                MERGE (a)-[:IN_CITY]->(ct)
                """,
                **params,
            )
        else:
            # Foreign and unidentified parties get their own key namespace but
            # keep the Company label, so existing traversals still reach them.
            await tx.run(
                f"""
                MATCH (c:Company {{cbe_number: $cbe_number}})
                UNWIND $rows AS row
                MERGE (other:ExternalEntity {{key: row.key}})
                ON CREATE SET other:Company,
                              other.denomination = row.name,
                              other.identifier = row.identifier,
                              other.country_code = row.country_code,
                              other._hydrated = false,
                              other._source = $source,
                              other._fetched_at = datetime()
                MERGE {pattern}
                SET r += row.props
                WITH other, row
                WHERE row.addr_key IS NOT NULL
                MERGE (a:Address {{key: row.addr_key}})
                SET a += row.addr_props, a._source = coalesce(a._source, $source)
                MERGE (other)-[:REGISTERED_AT]->(a)
                """,
                **params,
            )


async def ingest_financials(cbe_number: str, deposit: dict, metrics: dict) -> None:
    """Attach one filing's key figures to its Deposit node.

    Metrics live on the Deposit rather than the Company because they belong to
    a period, not to the company as it stands today — which is what makes a
    multi-year series a matter of reading several Deposit nodes rather than
    keeping a parallel history somewhere else.
    """
    await graph.run_write(
        """
        MERGE (c:Company {cbe_number: $cbe_number})
        ON CREATE SET c._hydrated = false, c._source = $source
        MERGE (d:Deposit {id: $deposit.id})
        SET d += $deposit,
            d += $metrics,
            d._source = $source,
            d._financials_fetched_at = datetime()
        MERGE (c)-[:FILED]->(d)
        """,
        cbe_number=cbe_number,
        deposit={k: v for k, v in deposit.items() if v is not None},
        metrics={k: v for k, v in metrics.items() if v is not None},
        source=CBSO_SOURCE,
    )


async def get_cached_financials(cbe_number: str) -> list[dict]:
    """Every filing we already hold figures for, oldest first."""
    return await graph.run_read(
        """
        MATCH (:Company {cbe_number: $cbe_number})-[:FILED]->(d:Deposit)
        WHERE d._financials_fetched_at IS NOT NULL
        RETURN d AS deposit ORDER BY d.period_end
        """,
        cbe_number=cbe_number,
    )


async def get_cached_deposit(cbe_number: str, ttl_days: int) -> dict | None:
    """Return the ownership already held for a company, if still within TTL."""
    rows = await graph.run_read(
        """
        MATCH (c:Company {cbe_number: $cbe_number})
        WHERE c._cbso_fetched_at > datetime() - duration({days: $ttl_days})
        OPTIONAL MATCH (c)-[:FILED]->(d:Deposit)
        WITH c, d ORDER BY d.period_end DESC
        WITH c, collect(d)[0] AS latest
        OPTIONAL MATCH (holder)-[s:SHAREHOLDER_OF]->(c)
        WITH c, latest, collect(DISTINCT {
            name: coalesce(holder.denomination, holder.name),
            cbe_number: holder.cbe_number, pct: s.pct, shares: s.shares,
            as_of: s.as_of
        }) AS shareholders
        OPTIONAL MATCH (officer)-[o:DIRECTOR_OF]->(c)
        WITH c, latest, shareholders, collect(DISTINCT {
            name: coalesce(officer.denomination, officer.name),
            cbe_number: officer.cbe_number, role: o.role_label,
            represented_by: o.represented_by, as_of: o.as_of
        }) AS directors
        OPTIONAL MATCH (c)-[h:HOLDS_PARTICIPATION]->(held)
        RETURN latest AS deposit, shareholders, directors,
               collect(DISTINCT {
                   name: coalesce(held.denomination, held.name),
                   cbe_number: held.cbe_number, pct: h.pct, as_of: h.as_of
               }) AS participations
        """,
        cbe_number=cbe_number,
        ttl_days=ttl_days,
    )
    return rows[0] if rows else None


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
