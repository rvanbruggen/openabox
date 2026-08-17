"""Tabular browsing of whatever the graph holds.

The canvas answers "how is this company connected?". This module answers the
other half — "what is in here at all?" — as sortable, filterable tables: every
company, every person, every address, and the two relationship tables
(shareholdings, directorships) that are otherwise reachable only one company at
a time via right-click.

**Everything here lists the local cache, not the register.** The CBE holds
around two million companies; this graph holds the ones that have been looked
up, plus the stubs that other companies' filings named. That is why every
entity carries a `scope` line and why `companies` exposes `_hydrated` as a
column: a stub has to be visibly a stub, or a table of 400 rows reads as a
claim about Belgium.

## Why a registry rather than eight endpoints

Cypher cannot parameterise a label or a property name — `MATCH (n:$label)` is
not a thing — so a generic table endpoint has to build its query as a string.
That is an injection surface which the read-transaction rule does *not* close:
a read query can still walk the entire store or call procedures.

So nothing from the client ever reaches the query text. The client sends
*keys*; the server looks them up here and uses the `expr` it wrote itself.
An unknown key is a 400, never an interpolation. Filter *values* are ordinary
Cypher parameters, and the query the table ran is returned to the client so it
can be opened in the Cypher console — the table teaches the console rather than
hiding it.
"""

from dataclasses import dataclass

# One page. Generous, because these tables are read by scrolling as often as by
# paging, but bounded so a hub label cannot pull the whole store into a browser.
MAX_LIMIT = 500

# A CSV export is a single query with no paging, so it needs its own ceiling.
# The client is told the real total alongside it and says which of the two it
# is offering, rather than handing over a silently truncated file.
EXPORT_LIMIT = 10_000

_SORT_DIRECTIONS = {"asc": "ASC", "desc": "DESC"}

# Longest first, so ">=" is not read as ">" with a stray "=".
_FILTER_OPS = (">=", "<=", "<>", ">", "<", "=")


class BrowseError(ValueError):
    """A bad entity, column, sort or filter — always the caller's mistake."""


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    #: Cypher expression, authored here and never assembled from user input.
    expr: str
    #: text | number | bool | date | list — drives both filtering and rendering.
    type: str = "text"
    sortable: bool = True
    #: Shown under the column header in the UI when the name is not enough.
    hint: str | None = None

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "sortable": self.sortable,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class Entity:
    key: str
    label: str
    #: The MATCH clause. One row per match — none of these fan out, so
    #: `count(*)` is the row count and needs no DISTINCT.
    match: str
    #: Identifies the row's primary node, so a row can be put on the canvas.
    id_expr: str
    columns: tuple[Column, ...]
    #: Expressions the single free-text box searches across.
    search: tuple[str, ...]
    default_sort: str
    default_dir: str = "asc"
    #: A CBE number, when the row has one: it lets a click load the full company
    #: rather than merely expanding a node.
    cbe_expr: str | None = None
    #: What this table is a list *of*, said plainly. Rendered above the rows.
    scope: str = ""
    #: A caveat that belongs with the data rather than with the UI.
    note: str | None = None

    def column(self, key: str) -> Column:
        for col in self.columns:
            if col.key == key:
                return col
        raise BrowseError(f"Unknown column '{key}' for {self.key}")

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "columns": [c.as_dict() for c in self.columns],
            "default_sort": self.default_sort,
            "default_dir": self.default_dir,
            "scope": self.scope,
            "note": self.note,
        }


# --------------------------------------------------------------------------
# Shared expression fragments
# --------------------------------------------------------------------------
#
# `head([(a)-[:R]->(b) | b.prop])` is a pattern comprehension: it reads the
# first neighbour's property without turning one row into many, which is what
# keeps `count(*)` an honest row count.

_CITY_OF = "head([(%s)-[:IN_CITY]->(ct:City) | trim(coalesce(ct.post_code, '') + ' ' + coalesce(ct.name, ''))])"
_COMPANY_CITY = (
    "head([(c)-[:REGISTERED_AT]->(:Address)-[:IN_CITY]->(ct:City) "
    "| trim(coalesce(ct.post_code, '') + ' ' + coalesce(ct.name, ''))])"
)

# A foreign shareholder carries :Company as well as :ExternalEntity, which is
# what keeps traversals working across the border — and is exactly why a table
# of companies has to say which is which.
_PARTY_KIND = (
    "CASE WHEN %(v)s:Person THEN 'person' "
    "WHEN %(v)s:ExternalEntity THEN 'foreign' ELSE 'company' END"
)
_ORIGIN = "CASE WHEN c:ExternalEntity THEN 'foreign' ELSE 'BE' END"


def _distinct_count(pattern: str, variable: str) -> str:
    """Count distinct *parties*, not distinct edges.

    Ownership edges are merged on `as_of`, so a company that has filed five
    years running carries five SHAREHOLDER_OF edges from the same owner. That
    is the point of the model — history accumulates instead of overwriting —
    but it means a plain edge count reports one owner as five. Every count
    that crosses a filing-derived edge therefore counts nodes.
    """
    return f"COUNT {{ MATCH {pattern} RETURN DISTINCT {variable} }}"


ENTITIES: dict[str, Entity] = {}


def _register(entity: Entity) -> Entity:
    ENTITIES[entity.key] = entity
    return entity


_register(Entity(
    key="companies",
    label="Companies",
    match="MATCH (c:Company)",
    id_expr="elementId(c)",
    cbe_expr="c.cbe_number",
    default_sort="name",
    scope="Companies held in the local graph — not the whole CBE register.",
    note=(
        "A row without a full record is a company another company's filing "
        "named. It exists; its details have not been fetched."
    ),
    search=("c.denomination", "c.cbe_number", "c.commercial_name", "c.abbreviation"),
    columns=(
        Column("name", "Name", "c.denomination"),
        Column("cbe", "CBE", "coalesce(c.cbe_number_formatted, c.cbe_number)"),
        Column("origin", "Register", _ORIGIN,
               hint="foreign = not in the Belgian register"),
        Column("status", "Status", "c.status"),
        Column("form", "Form",
               "head([(c)-[:HAS_FORM]->(f:JuridicalForm) "
               "| coalesce(f.short_label, f.label)])"),
        Column("nace", "NACE",
               "head([(c)-[:HAS_ACTIVITY]->(n:NaceCode) WHERE n.version = '2008' | n.code])"),
        Column("address", "Registered at",
               "head([(c)-[:REGISTERED_AT]->(a:Address) | a.full_address])"),
        Column("city", "City", _COMPANY_CITY),
        Column("establishments", "Est.", "COUNT { (c)-[:HAS_ESTABLISHMENT]->() }",
               type="number"),
        Column("shareholders", "Owners",
               _distinct_count("(c)<-[:SHAREHOLDER_OF]-(o)", "o"), type="number",
               hint="distinct owners across every filing held"),
        Column("directors", "Directors",
               _distinct_count("(c)<-[:DIRECTOR_OF]-(o)", "o"), type="number",
               hint="distinct officers across every filing held"),
        Column("filings", "Filings", "COUNT { (c)-[:FILED]->(:Deposit) }",
               type="number"),
        Column("hydrated", "Full record", "coalesce(c._hydrated, false)", type="bool"),
        Column("start_date", "Started", "c.start_date", type="date"),
        Column("fetched", "Cached", "c._fetched_at", type="date"),
    ),
))


_register(Entity(
    key="people",
    label="People",
    match="MATCH (p:Person)",
    id_expr="elementId(p)",
    default_sort="name",
    scope="Natural persons named as shareholders or directors in filed accounts.",
    note=(
        "People are keyed by name plus home post code, or — where the filing "
        "gives no address — by the company the mandate is held in, which "
        "deliberately confines them to that one company. Two rows with the "
        "same name may be one person or two; the graph does not guess. Home "
        "addresses come from the filings and are private data."
    ),
    search=("p.name", "p.last_name", "p.first_name"),
    columns=(
        Column("name", "Name", "p.name"),
        Column("last_name", "Surname", "p.last_name"),
        Column("first_name", "First name", "p.first_name"),
        Column("directorships", "Directorships",
               _distinct_count("(p)-[:DIRECTOR_OF]->(x)", "x"), type="number",
               hint="distinct companies, not filing years"),
        Column("shareholdings", "Shareholdings",
               _distinct_count("(p)-[:SHAREHOLDER_OF]->(x)", "x"), type="number",
               hint="distinct companies, not filing years"),
        Column("city", "Resides in",
               "head([(p)-[:RESIDES_AT]->(:Address)-[:IN_CITY]->(ct:City) "
               "| trim(coalesce(ct.post_code, '') + ' ' + coalesce(ct.name, ''))])"),
        Column("address", "Address",
               "head([(p)-[:RESIDES_AT]->(a:Address) | a.full_address])"),
        Column("key_basis", "Identified by", "p._key_basis",
               hint="post code, or confined to one company"),
        Column("key", "Key", "p.key"),
    ),
))


_register(Entity(
    key="addresses",
    label="Addresses",
    match="MATCH (a:Address)",
    id_expr="elementId(a)",
    default_sort="companies",
    default_dir="desc",
    scope="Buildings, keyed at building level — box numbers live on the establishment.",
    search=("a.full_address", "a.street", "a.key"),
    columns=(
        Column("address", "Address", "a.full_address"),
        Column("street", "Street", "a.street"),
        Column("number", "Nr", "a.street_number"),
        Column("city", "City", _CITY_OF % "a"),
        Column("country", "Country", "a.country_code"),
        Column("companies", "Registered",
               "COUNT { (a)<-[:REGISTERED_AT]-(:Company) }", type="number",
               hint="companies with their registered office here"),
        Column("establishments", "Branches",
               "COUNT { (a)<-[:LOCATED_AT]-(:Establishment) }", type="number"),
        Column("residents", "Residents",
               "COUNT { (a)<-[:RESIDES_AT]-(:Person) }", type="number"),
        Column("key", "Key", "a.key"),
    ),
))


_register(Entity(
    key="cities",
    label="Cities",
    match="MATCH (ct:City)",
    id_expr="elementId(ct)",
    default_sort="companies",
    default_dir="desc",
    scope="Postal areas, keyed by country and post code rather than by name.",
    note=(
        "One post code is filed under several names — 1040 appears as both "
        "Etterbeek and Brussel — so every spelling seen is kept as an alias "
        "and the name column shows only the first."
    ),
    search=("ct.name", "ct.post_code", "ct.key"),
    columns=(
        Column("post_code", "Post code", "ct.post_code"),
        Column("name", "Name", "ct.name"),
        Column("aliases", "Also filed as", "ct.aliases", type="list", sortable=False),
        Column("country", "Country", "ct.country_code"),
        Column("addresses", "Addresses", "COUNT { (ct)<-[:IN_CITY]-(:Address) }",
               type="number"),
        Column("companies", "Companies",
               _distinct_count(
                   "(ct)<-[:IN_CITY]-(:Address)<-[:REGISTERED_AT]-(c:Company)", "c"),
               type="number"),
        Column("key", "Key", "ct.key"),
    ),
))


_register(Entity(
    key="establishments",
    label="Establishments",
    match="MATCH (e:Establishment)",
    id_expr="elementId(e)",
    cbe_expr="head([(e)<-[:HAS_ESTABLISHMENT]-(c:Company) | c.cbe_number])",
    default_sort="company",
    scope="Branches and operating sites, each belonging to one company.",
    search=("e.establishment_number", "e.extra_info"),
    columns=(
        Column("number", "Establishment nr", "e.establishment_number"),
        Column("company", "Company",
               "head([(e)<-[:HAS_ESTABLISHMENT]-(c:Company) | c.denomination])"),
        Column("cbe", "CBE",
               "head([(e)<-[:HAS_ESTABLISHMENT]-(c:Company) | c.cbe_number])"),
        Column("address", "Address",
               "head([(e)-[:LOCATED_AT]->(a:Address) | a.full_address])"),
        Column("city", "City",
               "head([(e)-[:LOCATED_AT]->(:Address)-[:IN_CITY]->(ct:City) "
               "| trim(coalesce(ct.post_code, '') + ' ' + coalesce(ct.name, ''))])"),
        Column("box", "Box", "e.box"),
        Column("type", "Address type", "e.type_of_address"),
        Column("start_date", "Started", "e.start_date", type="date"),
        Column("struck_off", "Struck off", "e.date_striking_off", type="date"),
    ),
))


_register(Entity(
    key="nace",
    label="NACE codes",
    match="MATCH (n:NaceCode)",
    id_expr="elementId(n)",
    default_sort="companies",
    default_dir="desc",
    scope="Activity codes seen on cached companies.",
    note=(
        "The same code means different things in different NACE versions, so "
        "the version is part of the identity, not a detail."
    ),
    search=("n.code", "n.description"),
    columns=(
        Column("code", "Code", "n.code"),
        Column("version", "Version", "n.version"),
        Column("description", "Description", "n.description"),
        Column("companies", "Companies", "COUNT { (n)<-[:HAS_ACTIVITY]-(:Company) }",
               type="number"),
    ),
))


_register(Entity(
    key="forms",
    label="Legal forms",
    match="MATCH (f:JuridicalForm)",
    id_expr="elementId(f)",
    default_sort="companies",
    default_dir="desc",
    scope="Legal forms seen on cached companies.",
    search=("f.label", "f.short_label", "f.code"),
    columns=(
        Column("code", "Code", "f.code"),
        Column("short_label", "Short", "f.short_label"),
        Column("label", "Label", "f.label"),
        Column("companies", "Companies", "COUNT { (f)<-[:HAS_FORM]-(:Company) }",
               type="number"),
    ),
))


_register(Entity(
    key="situations",
    label="Legal situations",
    match="MATCH (s:JuridicalSituation)",
    id_expr="elementId(s)",
    default_sort="companies",
    default_dir="desc",
    scope="Registration statuses seen on cached companies.",
    search=("s.label", "s.code"),
    columns=(
        Column("code", "Code", "s.code"),
        Column("label", "Label", "s.label"),
        Column("companies", "Companies", "COUNT { (s)<-[:HAS_SITUATION]-(:Company) }",
               type="number"),
    ),
))


_register(Entity(
    key="deposits",
    label="Filings",
    match="MATCH (d:Deposit)",
    id_expr="elementId(d)",
    cbe_expr="head([(d)<-[:FILED]-(c:Company) | c.cbe_number])",
    default_sort="period_end",
    default_dir="desc",
    scope="Annual accounts fetched from the NBB, one row per filing.",
    note=(
        "Figures are as filed and not restated. A blank turnover is an "
        "abbreviated or micro filing, which does not disclose it — not a zero."
    ),
    search=("d.id", "d.model_id"),
    columns=(
        Column("company", "Company",
               "head([(d)<-[:FILED]-(c:Company) | c.denomination])"),
        Column("cbe", "CBE", "head([(d)<-[:FILED]-(c:Company) | c.cbe_number])"),
        Column("period_end", "Year end", "substring(toString(d.period_end), 0, 10)",
               type="date"),
        Column("model", "Model", "d.model_id",
               hint="m120… is a consolidated filing"),
        Column("file_type", "Format", "d.import_file_type"),
        Column("turnover", "Turnover", "d.turnover", type="number"),
        Column("result", "Result", "d.result", type="number"),
        Column("equity", "Equity", "d.equity", type="number"),
        Column("total_assets", "Total assets", "d.total_assets", type="number"),
        Column("employees_fte", "FTE", "d.employees_fte", type="number"),
        Column("id", "Filing id", "d.id"),
    ),
))


# --------------------------------------------------------------------------
# Relationship tables
# --------------------------------------------------------------------------
#
# These are the rows that have no home on the canvas: a shareholding is an
# *edge*, and the UI can otherwise only show one company's edges at a time.
# Both carry `as_of`, because ownership is only ever true as of a filing date.

_register(Entity(
    key="shareholdings",
    label="Shareholdings",
    match="MATCH (owner)-[s:SHAREHOLDER_OF]->(held:Company)",
    id_expr="elementId(held)",
    cbe_expr="held.cbe_number",
    default_sort="pct",
    default_dir="desc",
    scope="Every shareholding in the graph, one row per owner per filing year.",
    note=(
        "A company can appear twice for the same owner with different years: "
        "filings accumulate rather than overwrite, which is the point. "
        "Percentages are as filed for that year, not today's ownership."
    ),
    search=("owner.denomination", "owner.name", "held.denomination", "held.cbe_number"),
    columns=(
        Column("owner", "Owner", "coalesce(owner.denomination, owner.name)"),
        Column("owner_kind", "Owner is", _PARTY_KIND % {"v": "owner"}),
        Column("owner_cbe", "Owner CBE", "owner.cbe_number"),
        Column("pct", "Stake %", "s.pct", type="number",
               hint="≥25 blocks, >50 controls"),
        Column("company", "Company", "held.denomination"),
        Column("company_cbe", "CBE", "held.cbe_number"),
        Column("shares", "Shares", "s.shares", type="number"),
        Column("share_nature", "Share class", "s.share_nature"),
        Column("as_of", "As of", "s.as_of", type="date"),
        Column("deposit", "From filing", "s._deposit"),
    ),
))


_register(Entity(
    key="directorships",
    label="Directorships",
    match="MATCH (officer)-[d:DIRECTOR_OF]->(c:Company)",
    id_expr="elementId(c)",
    cbe_expr="c.cbe_number",
    default_sort="officer",
    scope="Every board mandate in the graph, one row per officer per filing year.",
    note=(
        "A directorship is control without ownership. An officer can itself be "
        "a company, in which case the filing usually names the individual "
        "representing it."
    ),
    search=("officer.denomination", "officer.name", "c.denomination", "d.role_label"),
    columns=(
        Column("officer", "Officer", "coalesce(officer.denomination, officer.name)"),
        Column("officer_kind", "Officer is", _PARTY_KIND % {"v": "officer"}),
        Column("officer_cbe", "Officer CBE", "officer.cbe_number"),
        Column("role", "Role", "d.role_label"),
        Column("company", "Company", "c.denomination"),
        Column("company_cbe", "CBE", "c.cbe_number"),
        Column("represented_by", "Represented by", "d.represented_by",
               type="list", sortable=False),
        Column("as_of", "As of", "d.as_of", type="date"),
        Column("deposit", "From filing", "d._deposit"),
    ),
))


# --------------------------------------------------------------------------
# Query building
# --------------------------------------------------------------------------


def entity(key: str) -> Entity:
    try:
        return ENTITIES[key]
    except KeyError:
        raise BrowseError(f"Unknown table '{key}'") from None


def _text_match(expr: str, param: str) -> str:
    """Case-insensitive substring match that survives nulls and non-strings."""
    return f"toLower(toString(coalesce({expr}, ''))) CONTAINS ${param}"


def _number_clause(expr: str, raw: str, param: str) -> tuple[str, float]:
    """Parse `>= 25`, `<1000`, `=0` — or a bare number, read as "at least".

    "at least" rather than "exactly" because these columns are mostly counts,
    and the question behind typing 1 into an Owners column is "which ones have
    any?", not "which ones have precisely one?".
    """
    for candidate in _FILTER_OPS:
        if raw.startswith(candidate):
            op, value = candidate, raw[len(candidate):].strip()
            break
    else:
        op, value = ">=", raw
    try:
        return f"{expr} {op} ${param}", float(value)
    except ValueError:
        raise BrowseError(f"'{raw}' is not a number or comparison") from None


def _where(ent: Entity, q: str | None, filters: dict[str, str]) -> tuple[str, dict]:
    clauses: list[str] = []
    params: dict[str, object] = {}

    if q and q.strip():
        params["q"] = q.strip().lower()
        clauses.append(
            "(" + " OR ".join(_text_match(e, "q") for e in ent.search) + ")"
        )

    for index, (key, raw) in enumerate(sorted(filters.items())):
        raw = (raw or "").strip()
        if not raw:
            continue
        col = ent.column(key)
        param = f"flt{index}"

        if col.type == "number":
            clause, value = _number_clause(col.expr, raw, param)
            clauses.append(clause)
            params[param] = value
        elif col.type == "bool":
            wanted = raw.lower() in ("true", "yes", "1", "y")
            clauses.append(f"coalesce({col.expr}, false) = ${param}")
            params[param] = wanted
        elif col.type == "list":
            # toString() throws on a list, so a list column is searched by
            # element rather than by its rendered form.
            clauses.append(
                f"any(x IN coalesce({col.expr}, []) "
                f"WHERE toLower(toString(x)) CONTAINS ${param})"
            )
            params[param] = raw.lower()
        else:
            clauses.append(_text_match(col.expr, param))
            params[param] = raw.lower()

    return ("WHERE " + " AND ".join(clauses) if clauses else ""), params


def _order_by(ent: Entity, sort: str | None, direction: str | None) -> str:
    col = ent.column(sort or ent.default_sort)
    if not col.sortable:
        raise BrowseError(f"Column '{col.key}' cannot be sorted")
    keyword = _SORT_DIRECTIONS.get((direction or ent.default_dir).lower())
    if keyword is None:
        raise BrowseError("Sort direction must be asc or desc")
    # Ordering on the returned alias rather than re-stating the expression, so
    # a COUNT{} column is evaluated once per row instead of twice.
    return f"ORDER BY `{col.key}` {keyword}"


def _return(ent: Entity) -> str:
    parts = [f"{ent.id_expr} AS _id"]
    if ent.cbe_expr:
        parts.append(f"{ent.cbe_expr} AS _cbe")
    parts += [f"{c.expr} AS `{c.key}`" for c in ent.columns]
    return "RETURN " + ",\n       ".join(parts)


def rows_query(
    ent: Entity,
    *,
    sort: str | None = None,
    direction: str | None = None,
    q: str | None = None,
    filters: dict[str, str] | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[str, dict]:
    where, params = _where(ent, q, filters or {})
    query = "\n".join(
        part for part in (
            ent.match,
            where,
            _return(ent),
            _order_by(ent, sort, direction),
            "SKIP $skip LIMIT $limit",
        ) if part
    )
    return query, params | {"skip": skip, "limit": limit}


def count_query(
    ent: Entity, *, q: str | None = None, filters: dict[str, str] | None = None
) -> tuple[str, dict]:
    where, params = _where(ent, q, filters or {})
    query = "\n".join(
        part for part in (ent.match, where, "RETURN count(*) AS total") if part
    )
    return query, params


def to_csv_value(value) -> str:
    """Flatten one cell for CSV.

    Lists are joined rather than dumped as JSON, because the columns that hold
    them (city aliases, the people representing a corporate director) are read
    as "these several things", not as a data structure.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(to_csv_value(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
