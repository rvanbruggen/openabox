"""XBRL party-extraction tests.

These guard the two things that make the extractor fragile. First, meaning in a
CBSO filing lives entirely in context dimensions, so a single wrong constant
turns shareholders into silence rather than into an error. Second, several
attributes are filed *twice* on the same axis under different `spec` members,
so a naive "last fact wins" read produces plausible, wrong numbers.

The fixture below is synthetic but structurally identical to a real filing —
same namespaces, same dimensions, same members — so it exercises the real code
path without carrying a megabyte of someone's accounts in the repo.
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.identity import external_key, person_key  # noqa: E402
from app.xbrl import normalise_cbe, parse_parties, summarise  # noqa: E402

NS = """
xmlns="http://www.xbrl.org/2003/instance"
xmlns:link="http://www.xbrl.org/2003/linkbase"
xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
xmlns:dim="http://www.nbb.be/be/fr/cbso/dict/dim"
xmlns:met="http://www.nbb.be/be/fr/cbso/dict/met"
xmlns:psn="http://www.nbb.be/be/fr/cbso/dict/dom/psn"
xmlns:bas="http://www.nbb.be/be/fr/cbso/dict/dom/bas"
xmlns:ctc="http://www.nbb.be/be/fr/cbso/dict/dom/ctc"
xmlns:qlt="http://www.nbb.be/be/fr/cbso/dict/dom/qlt"
xmlns:spec="http://www.nbb.be/be/fr/cbso/dict/dom/spec"
xmlns:cty="http://www.nbb.be/be/fr/cbso/dict/dom/cty"
xmlns:pcd="http://www.nbb.be/be/fr/cbso/dict/dom/pcd"
"""


def _context(cid: str, typed: dict, explicit: dict) -> str:
    members = "".join(
        f'<xbrldi:typedMember dimension="{d}"><x>{v}</x></xbrldi:typedMember>'
        for d, v in typed.items()
    ) + "".join(
        f'<xbrldi:explicitMember dimension="{d}">{v}</xbrldi:explicitMember>'
        for d, v in explicit.items()
    )
    return (
        f'<context id="{cid}"><entity><identifier scheme="http://x">0</identifier>'
        f"<segment>{members}</segment></entity>"
        f"<period><instant>2025-03-31</instant></period></context>"
    )


def _doc(parts: list[str]) -> io.BytesIO:
    return io.BytesIO(f"<xbrl {NS}>{''.join(parts)}</xbrl>".encode())


def _shareholder_fixture() -> io.BytesIO:
    """One legal-person and one natural-person shareholder."""
    legal = {"dim:snlp": "Korys NV"}
    natural = {"dim:snnp": "Peeters", "dim:sfnp": "Jan"}
    return _doc(
        [
            _context("c1", legal, {"dim:psn": "psn:m20", "dim:bas": "bas:m26",
                                   "dim:qlt": "qlt:m1"}),
            _context("c2", legal, {"dim:psn": "psn:m20", "dim:bas": "bas:m117"}),
            # Share counts arrive twice: linked to securities (the holding) and
            # not linked (usually zero). Order is deliberately "wrong" here.
            _context("c3", legal, {"dim:psn": "psn:m20", "dim:bas": "bas:m71",
                                   "dim:spec": "spec:m88"}),
            _context("c4", legal, {"dim:psn": "psn:m20", "dim:bas": "bas:m71",
                                   "dim:spec": "spec:m89"}),
            _context("c5", natural, {"dim:psn": "psn:m22", "dim:bas": "bas:m117"}),
            _context("c6", natural, {"dim:psn": "psn:m22", "dim:bas": "bas:m31",
                                     "dim:ctc": "ctc:m4"}),
            '<met:str2 contextRef="c1">0844198918</met:str2>',
            '<met:pct1 contextRef="c2">0.6444</met:pct1>',
            '<met:int2 contextRef="c3">82065193</met:int2>',
            '<met:int2 contextRef="c4">0</met:int2>',
            '<met:pct1 contextRef="c5">0.0668</met:pct1>',
            '<met:list1 contextRef="c6">pcd:m1500</met:list1>',
        ]
    )


def test_legal_person_shareholder_is_extracted_with_cbe_number():
    parties = {p["name"]: p for p in parse_parties(_shareholder_fixture())}
    korys = parties["Korys NV"]
    assert korys["role"] == "SHAREHOLDER"
    assert korys["kind"] == "company"
    assert korys["cbe_number"] == "0844198918"


def test_percentages_are_converted_from_fractions():
    """Filings store 0.6444; everything downstream expects 64.44."""
    parties = {p["name"]: p for p in parse_parties(_shareholder_fixture())}
    assert parties["Korys NV"]["pct"] == 64.44
    assert parties["Jan Peeters"]["pct"] == 6.68


def test_share_count_ignores_rights_not_linked_to_securities():
    """The regression that matters: spec:m89 must not overwrite spec:m88.

    Both are filed on the voting-rights axis for the same shareholder, and the
    "not linked" figure is nearly always 0 — so taking the last fact silently
    zeroes every shareholding in the graph.
    """
    parties = {p["name"]: p for p in parse_parties(_shareholder_fixture())}
    assert parties["Korys NV"]["shares"] == 82065193
    assert parties["Korys NV"]["voting_rights_unlinked"] == 0


def test_natural_person_shareholder_keeps_name_parts_and_address():
    parties = {p["name"]: p for p in parse_parties(_shareholder_fixture())}
    jan = parties["Jan Peeters"]
    assert jan["kind"] == "person"
    assert (jan["first_name"], jan["last_name"]) == ("Jan", "Peeters")
    assert jan["cbe_number"] is None
    # Members arrive prefixed and numbered — pcd:m1500 is post code 1500.
    assert jan["address"]["post_code"] == "1500"


def test_participation_splits_direct_from_via_subsidiaries():
    doc = _doc(
        [
            _context("p1", {"dim:ptpn": "Colim"},
                     {"dim:psn": "psn:m1", "dim:bas": "bas:m117",
                      "dim:spec": "spec:m87"}),
            _context("p2", {"dim:ptpn": "Colim"},
                     {"dim:psn": "psn:m1", "dim:bas": "bas:m117",
                      "dim:spec": "spec:m32"}),
            '<met:pct1 contextRef="p1">0.9997</met:pct1>',
            '<met:pct1 contextRef="p2">0.0003</met:pct1>',
        ]
    )
    colim = parse_parties(doc)[0]
    assert colim["role"] == "PARTICIPATION"
    assert colim["pct"] == 99.97
    assert colim["pct_via_subsidiaries"] == 0.03


def test_representative_is_folded_onto_the_legal_person_director():
    """A corporate director's signatory is not a director in their own right."""
    doc = _doc(
        [
            _context("d1", {"dim:anlp": "Korys NV"},
                     {"dim:psn": "psn:m10", "dim:bas": "bas:m26",
                      "dim:qlt": "qlt:m1"}),
            _context("d2", {"dim:anlp": "Korys NV", "dim:aprn": "Aerts",
                            "dim:aprf": "Griet"},
                     {"dim:psn": "psn:m11", "dim:bas": "bas:m31",
                      "dim:ctc": "ctc:m1"}),
            '<met:str2 contextRef="d1">0844198918</met:str2>',
            '<met:str2 contextRef="d2">Villalaan</met:str2>',
        ]
    )
    parties = parse_parties(doc)
    assert len(parties) == 1
    assert parties[0]["role"] == "DIRECTOR"
    assert parties[0]["represented_by"] == ["Griet Aerts"]
    # The representative's address must not land on the company.
    assert not parties[0]["address"]


def test_summarise_counts_by_role():
    assert summarise(parse_parties(_shareholder_fixture())) == {"SHAREHOLDER": 2}


def test_foreign_identifiers_are_not_mistaken_for_cbe_numbers():
    assert normalise_cbe("0844198918") == "0844198918"
    assert normalise_cbe("0844.198.918") == "0844198918"
    assert normalise_cbe("BE0844198918") == "0844198918"
    assert normalise_cbe("CHE-109.996.904") is None
    assert normalise_cbe("NO 0917351538") is None
    assert normalise_cbe(None) is None


def test_person_key_uses_post_code_when_available():
    """Same person, two companies, same home post code -> one node."""
    a = person_key("Colruyt", "Jef", {"post_code": "1670"}, "0400378485")
    b = person_key("colruyt", "JEF", {"post_code": "1670"}, "0779301067")
    assert a == b
    assert a[1] == "post_code"


def test_person_key_falls_back_to_the_company_when_no_address():
    """Without an address the key is confined to one company, on purpose.

    Merging "Jan Peeters" across companies on the name alone would invent
    relationships between unrelated businesses.
    """
    a = person_key("Peeters", "Jan", None, "0400378485")
    b = person_key("Peeters", "Jan", None, "0779301067")
    assert a != b
    assert a[1] == "company"


def test_person_key_needs_at_least_one_name_part():
    assert person_key(None, None, {"post_code": "1000"}, "0400378485") is None


def test_external_key_separates_countries_and_marks_name_only_keys():
    assert external_key("CHE-109.996.904", "CH", "Stiftung") == "CH|che109996904"
    assert external_key(None, "DE", "Viva la Faba GmbH") == "DE|name:viva la faba gmbh"
    assert external_key(None, None, None) is None


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
    print(f"\n{'FAILED' if failures else 'All XBRL tests passed'} ({failures} failure(s))")
    sys.exit(1 if failures else 0)
