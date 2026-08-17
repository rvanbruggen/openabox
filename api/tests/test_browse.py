"""Table-browser query-building tests.

Two failure modes are guarded here, and only the first is obvious.

The obvious one is **injection**: Cypher cannot parameterise a label or a
property name, so the browse endpoint builds its query as a string. Read-only
transactions do not close that hole — a read query can still walk the whole
store. Every entity, column and sort key must therefore be resolved against
the registry and rejected if unknown, and every filter *value* must leave as a
parameter rather than as text.

The quiet one is **double counting**. Ownership edges are merged on `as_of`, so
a company that has filed five years running carries five SHAREHOLDER_OF edges
from the same owner. A plain edge count reports one owner as five, which looks
entirely plausible in a table and is wrong.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import browse  # noqa: E402

COUNT_COLUMNS = {
    ("companies", "shareholders"),
    ("companies", "directors"),
    ("people", "directorships"),
    ("people", "shareholdings"),
    ("cities", "companies"),
}


def _raises(fn, *args, **kwargs) -> str:
    try:
        fn(*args, **kwargs)
    except browse.BrowseError as exc:
        return str(exc)
    raise AssertionError("expected a BrowseError")


# --------------------------------------------------------------------------
# Registry integrity
# --------------------------------------------------------------------------


def test_every_entity_sorts_on_a_real_sortable_column():
    for key, ent in browse.ENTITIES.items():
        col = ent.column(ent.default_sort)  # raises if it does not exist
        assert col.sortable, f"{key} defaults to unsortable column {col.key}"
        assert ent.default_dir in ("asc", "desc"), key


def test_column_keys_are_unique_per_entity():
    for key, ent in browse.ENTITIES.items():
        keys = [c.key for c in ent.columns]
        assert len(keys) == len(set(keys)), f"{key} has duplicate column keys"


def test_list_columns_are_never_sortable():
    """ORDER BY on a list column is meaningless, and toString() throws on one."""
    for key, ent in browse.ENTITIES.items():
        for col in ent.columns:
            if col.type == "list":
                assert not col.sortable, f"{key}.{col.key} is a sortable list"


def test_every_column_reaches_the_return_clause():
    for ent in browse.ENTITIES.values():
        query, _ = browse.rows_query(ent)
        for col in ent.columns:
            assert f"AS `{col.key}`" in query, f"{ent.key}.{col.key} missing"


# --------------------------------------------------------------------------
# Nothing from the caller reaches the query text
# --------------------------------------------------------------------------


def test_unknown_table_is_rejected():
    assert "Unknown table" in _raises(browse.entity, "Company) DETACH DELETE (c")


def test_unknown_column_is_rejected_not_interpolated():
    ent = browse.entity("companies")
    message = _raises(browse.rows_query, ent, filters={"c) RETURN 1 //": "x"})
    assert "Unknown column" in message


def test_unknown_sort_is_rejected():
    ent = browse.entity("companies")
    assert "Unknown column" in _raises(browse.rows_query, ent, sort="name DESC, 1")


def test_unsortable_column_is_rejected():
    ent = browse.entity("cities")
    assert "cannot be sorted" in _raises(browse.rows_query, ent, sort="aliases")


def test_bad_sort_direction_is_rejected():
    ent = browse.entity("companies")
    assert "asc or desc" in _raises(browse.rows_query, ent, direction="asc; MATCH")


def test_filter_values_travel_as_parameters():
    ent = browse.entity("companies")
    hostile = "' OR true //"
    query, params = browse.rows_query(ent, q=hostile, filters={"status": hostile})
    assert hostile not in query
    assert hostile.lower() in params.values()


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


def test_number_filter_parses_comparisons():
    ent = browse.entity("shareholdings")
    for raw, op, value in [(">=25", ">=", 25.0), ("<50", "<", 50.0),
                           ("=0", "=", 0.0), ("> 5", ">", 5.0)]:
        query, params = browse.rows_query(ent, filters={"pct": raw})
        assert f"s.pct {op} $flt0" in query, raw
        assert params["flt0"] == value, raw


def test_bare_number_reads_as_at_least():
    """Typing 1 into an Owners column asks "which have any?", not "exactly one"."""
    query, params = browse.rows_query(browse.entity("shareholdings"),
                                      filters={"pct": "25"})
    assert "s.pct >= $flt0" in query
    assert params["flt0"] == 25.0


def test_non_numeric_filter_on_a_number_column_is_rejected():
    ent = browse.entity("shareholdings")
    assert "not a number" in _raises(browse.rows_query, ent, filters={"pct": "lots"})


def test_bool_filter_matches_missing_as_false():
    """A stub company has no _hydrated at all; it must still answer "no"."""
    query, params = browse.rows_query(browse.entity("companies"),
                                      filters={"hydrated": "false"})
    assert "coalesce(coalesce(c._hydrated, false), false) = $flt0" in query
    assert params["flt0"] is False


def test_list_filter_searches_elements_not_the_rendered_form():
    query, _ = browse.rows_query(browse.entity("cities"),
                                 filters={"aliases": "brussel"})
    assert "any(x IN coalesce(ct.aliases, [])" in query
    assert "toString(ct.aliases)" not in query


def test_blank_filters_add_no_clause():
    # Checked line-wise, not by substring: pattern comprehensions carry their
    # own inner WHERE inside the RETURN clause, which is not a filter.
    query, params = browse.rows_query(browse.entity("companies"),
                                      q="  ", filters={"status": "", "name": "  "})
    assert not any(line.startswith("WHERE") for line in query.split("\n"))
    assert set(params) == {"skip", "limit"}


def test_free_text_search_covers_every_declared_expression():
    ent = browse.entity("companies")
    query, _ = browse.rows_query(ent, q="colruyt")
    for expr in ent.search:
        assert expr in query


def test_count_query_shares_the_filters_but_not_the_paging():
    ent = browse.entity("companies")
    rows, row_params = browse.rows_query(ent, q="x", filters={"status": "AC"})
    counting, count_params = browse.count_query(ent, q="x", filters={"status": "AC"})
    assert counting.startswith(ent.match)
    assert "RETURN count(*) AS total" in counting
    assert "SKIP" not in counting
    # The same WHERE, so the total describes the rows actually being paged.
    where = rows.split("\n")[1]
    assert where in counting
    assert count_params == {k: v for k, v in row_params.items()
                            if k not in ("skip", "limit")}


# --------------------------------------------------------------------------
# Counting parties, not filing years
# --------------------------------------------------------------------------


def test_counts_across_filing_edges_are_distinct_on_the_node():
    for entity_key, column_key in COUNT_COLUMNS:
        col = browse.entity(entity_key).column(column_key)
        assert "RETURN DISTINCT" in col.expr, f"{entity_key}.{column_key} counts edges"


def test_structural_counts_need_no_distinct():
    """A company has one HAS_ESTABLISHMENT edge per establishment, not per year."""
    col = browse.entity("companies").column("establishments")
    assert "DISTINCT" not in col.expr


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------


def test_csv_flattens_lists_and_blanks_nulls():
    assert browse.to_csv_value(None) == ""
    assert browse.to_csv_value(["Etterbeek", "Brussel"]) == "Etterbeek; Brussel"
    assert browse.to_csv_value(True) == "true"
    assert browse.to_csv_value(0) == "0"


def test_csv_keeps_zero_distinct_from_absent():
    """A blank turnover is "not disclosed"; 0 is a filed zero. Never conflate."""
    assert browse.to_csv_value(0.0) == "0.0"
    assert browse.to_csv_value(None) == ""


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL  {name}: {exc}")
    print(f"\n{'FAILED' if failures else 'All browse tests passed'} ({failures} failure(s))")
    sys.exit(1 if failures else 0)
